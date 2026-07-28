import csv
import io
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Usuario

from .models import CondicaoEquipamento, EntradaLote, Equipamento, StatusEquipamento, TipoEquipamento


BATCH_SIZE = 1000
MAX_ERROR_LINES = 200


STATUS_MAP = {
    'ativo': StatusEquipamento.EM_USO,
    'em_uso': StatusEquipamento.EM_USO,
    'uso': StatusEquipamento.EM_USO,
    'estoque': StatusEquipamento.EM_ESTOQUE,
    'em_estoque': StatusEquipamento.EM_ESTOQUE,
    'reservado': StatusEquipamento.RESERVADO,
    'manutencao': StatusEquipamento.EM_MANUTENCAO,
    'em_manutencao': StatusEquipamento.EM_MANUTENCAO,
    'baixado': StatusEquipamento.DESCARTADO,
    'descartado': StatusEquipamento.DESCARTADO,
    'aguardando': StatusEquipamento.AGUARDANDO_APROVACAO,
    'aguardando_aprovacao': StatusEquipamento.AGUARDANDO_APROVACAO,
}


CONDICAO_MAP = {
    'novo': CondicaoEquipamento.OTIMO,
    'otimo': CondicaoEquipamento.OTIMO,
    'otima': CondicaoEquipamento.OTIMO,
    'bom': CondicaoEquipamento.BOM,
    'regular': CondicaoEquipamento.REGULAR,
    'ruim': CondicaoEquipamento.RUIM,
    'inutil': CondicaoEquipamento.INUTIL,
}


TIPO_VALUES = {value for value, _ in TipoEquipamento.choices}


PERSON_TITLES = {'sr', 'sra', 'srta', 'dr', 'dra', 'prof', 'profa'}


def aplicar_movimentacao_equipamento(equipamento, movimentacao):
    tipo = movimentacao.tipo

    if tipo == 'entrada':
        equipamento.status = StatusEquipamento.EM_ESTOQUE
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'reserva':
        equipamento.status = StatusEquipamento.RESERVADO
        equipamento.data_atribuicao = None
    elif tipo == 'liberacao_reserva':
        equipamento.status = StatusEquipamento.EM_ESTOQUE
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'saida':
        equipamento.status = StatusEquipamento.EM_USO
        equipamento.responsavel = movimentacao.usuario_novo or equipamento.responsavel
        equipamento.data_atribuicao = timezone.now()
    elif tipo == 'devolucao':
        equipamento.status = StatusEquipamento.EM_ESTOQUE
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'manutencao':
        equipamento.status = StatusEquipamento.EM_MANUTENCAO
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'retorno_manutencao':
        equipamento.status = StatusEquipamento.EM_ESTOQUE
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'descarte':
        equipamento.status = StatusEquipamento.DESCARTADO
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo in {'transferencia', 'troca'} and movimentacao.usuario_novo:
        equipamento.status = StatusEquipamento.EM_USO
        equipamento.responsavel = movimentacao.usuario_novo
        equipamento.data_atribuicao = timezone.now()

    equipamento.save()


def _normalize_key(value):
    if value is None:
        return ''
    text = str(value).strip().lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'\s+', ' ', text)


def _normalize_person_name(value):
    tokens = [token for token in _normalize_key(value).split() if token and token not in PERSON_TITLES]
    return ' '.join(tokens)


def _clean(value):
    return (value or '').strip()


def _parse_date(value):
    value = _clean(value)
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValidationError(f'Data inválida: {value}')


def _parse_decimal(value):
    value = _clean(value)
    if not value:
        return None
    try:
        return Decimal(value.replace(',', '.'))
    except InvalidOperation as exc:
        raise ValidationError(f'Valor inválido: {value}') from exc


def _parse_int(value, default=None):
    value = _clean(value)
    if not value:
        return default
    try:
        return int(float(value.replace(',', '.')))
    except ValueError as exc:
        raise ValidationError(f'Inteiro inválido: {value}') from exc


def _parse_float(value, default=100.0):
    value = _clean(value)
    if not value:
        return default
    try:
        return float(value.replace(',', '.'))
    except ValueError as exc:
        raise ValidationError(f'Número inválido: {value}') from exc


def _map_tipo(tipo_raw, tipo_outro_raw=''):
    tipo = _normalize_key(tipo_raw).replace('-', '_')
    if tipo in TIPO_VALUES:
        if tipo == TipoEquipamento.OUTRO:
            return tipo, _clean(tipo_outro_raw) or _clean(tipo_raw)
        return tipo, _clean(tipo_outro_raw)
    if tipo:
        return TipoEquipamento.OUTRO, _clean(tipo_outro_raw) or _clean(tipo_raw)
    return TipoEquipamento.OUTRO, _clean(tipo_outro_raw)


def _map_status(status_raw):
    return STATUS_MAP.get(_normalize_key(status_raw), StatusEquipamento.EM_ESTOQUE)


def _map_condicao(condicao_raw):
    return CONDICAO_MAP.get(_normalize_key(condicao_raw), CondicaoEquipamento.BOM)


def _build_user_lookup():
    lookup = {}
    for usuario in Usuario.objects.only('id', 'matricula', 'username', 'first_name', 'last_name', 'is_superuser'):
        keys = {
            _normalize_key(usuario.matricula),
            _normalize_key(usuario.username),
            _normalize_person_name(f'{usuario.first_name} {usuario.last_name}'),
        }
        for key in filter(None, keys):
            lookup.setdefault(key, usuario)
    return lookup


def _resolve_responsavel(value, lookup):
    key = _normalize_person_name(value)
    if not key:
        return None
    if key in lookup:
        return lookup[key]

    # Try simple fallbacks if the file contains only first or last names.
    parts = key.split()
    if parts:
        if parts[0] in lookup:
            return lookup[parts[0]]
        joined = ' '.join(parts[:2])
        if joined in lookup:
            return lookup[joined]
    return None


def _read_uploaded_csv(file_obj):
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)
    raw_data = file_obj.read()
    if isinstance(raw_data, str):
        return raw_data

    for encoding in ('utf-8-sig', 'utf-8', 'cp1252'):
        try:
            return raw_data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError('Não foi possível ler o CSV. Verifique a codificação do arquivo.')


def _prepare_row(row, responsavel_lookup):
    id_patrimonio = _clean(row.get('id_patrimonio'))
    if not id_patrimonio:
        raise ValidationError('Patrimônio vazio.')

    tipo, tipo_outro = _map_tipo(row.get('tipo'), row.get('tipo_outro'))
    responsavel = _resolve_responsavel(row.get('responsavel'), responsavel_lookup)

    return {
        'id_patrimonio': id_patrimonio,
        'tipo': tipo,
        'tipo_outro': tipo_outro,
        'marca': _clean(row.get('marca')),
        'modelo': _clean(row.get('modelo')),
        'service_tag': _clean(row.get('service_tag')),
        'imei': _clean(row.get('imei')),
        'numero_serie': _clean(row.get('numero_serie')),
        'monitor_patrimonio': _clean(row.get('monitor_patrimonio')),
        'status': _map_status(row.get('status')),
        'condicao': _map_condicao(row.get('condicao')),
        'responsavel': responsavel,
        'site': _clean(row.get('site')),
        'setor': _clean(row.get('setor')),
        'andar_sala': _clean(row.get('andar_sala')),
        'descricao': _clean(row.get('descricao')),
        'data_aquisicao': _parse_date(row.get('data_aquisicao')),
        'valor_aquisicao': _parse_decimal(row.get('valor_aquisicao')),
        'garantia_ate': _parse_date(row.get('garantia_ate')),
        'vida_util_estimada_meses': _parse_int(row.get('vida_util_estimada_meses'), default=36),
        'score_saude': _parse_float(row.get('score_saude'), default=100.0),
    }


def _process_batch(batch_rows, responsavel_lookup, criado_por):
    prepared = {}
    errors = 0
    error_lines = []

    for line_number, row in batch_rows:
        try:
            data = _prepare_row(row, responsavel_lookup)
        except ValidationError as exc:
            errors += 1
            error_lines.append(f'Linha {line_number}: {exc.messages[0]}')
            continue

        if data['id_patrimonio'] in prepared:
            error_lines.append(
                f'Linha {line_number}: patrimônio duplicado no mesmo lote ({data["id_patrimonio"]}). Mantida a última ocorrência.'
            )
        prepared[data['id_patrimonio']] = (line_number, data)

    if not prepared:
        return {'created': 0, 'updated': 0, 'errors': errors, 'error_lines': error_lines}

    existentes = Equipamento.objects.in_bulk(prepared.keys(), field_name='id_patrimonio')
    agora = timezone.now()
    to_create = []
    to_update = []

    for _, data in prepared.values():
        equipamento = existentes.get(data['id_patrimonio'])
        if equipamento is None:
            equipamento = Equipamento(**data)
            equipamento.criado_por = criado_por
            equipamento.created_at = agora
            equipamento.updated_at = agora
            to_create.append(equipamento)
            continue

        for field, value in data.items():
            setattr(equipamento, field, value)
        equipamento.updated_at = agora
        to_update.append(equipamento)

    with transaction.atomic():
        if to_create:
            Equipamento.objects.bulk_create(to_create, batch_size=BATCH_SIZE)
        if to_update:
            Equipamento.objects.bulk_update(
                to_update,
                fields=[
                    'tipo',
                    'tipo_outro',
                    'marca',
                    'modelo',
                    'service_tag',
                    'imei',
                    'numero_serie',
                    'monitor_patrimonio',
                    'status',
                    'condicao',
                    'responsavel',
                    'site',
                    'setor',
                    'andar_sala',
                    'descricao',
                    'data_aquisicao',
                    'valor_aquisicao',
                    'garantia_ate',
                    'vida_util_estimada_meses',
                    'score_saude',
                    'updated_at',
                ],
                batch_size=BATCH_SIZE,
            )

    return {
        'created': len(to_create),
        'updated': len(to_update),
        'errors': errors,
        'error_lines': error_lines,
    }


def importar_equipamentos_csv(arquivo, criado_por=None, descricao=''):
    if arquivo is None:
        raise ValidationError('Informe um arquivo CSV.')

    lote = EntradaLote.objects.create(
        arquivo=arquivo,
        descricao=descricao or '',
        total_itens=0,
        itens_importados=0,
        itens_com_erro=0,
        status='processando',
        criado_por=criado_por,
    )

    responsavel_lookup = _build_user_lookup()
    total_linhas = 0
    total_criados = 0
    total_atualizados = 0
    total_erros = 0
    error_lines = []

    try:
        with lote.arquivo.open('rb') as handle:
            csv_text = _read_uploaded_csv(handle)

        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            raise ValidationError('O CSV está vazio.')
        if 'id_patrimonio' not in [field.strip() for field in reader.fieldnames if field]:
            raise ValidationError('O CSV precisa conter a coluna id_patrimonio.')

        batch_rows = []
        for line_number, row in enumerate(reader, start=2):
            total_linhas += 1
            batch_rows.append((line_number, row))
            if len(batch_rows) >= BATCH_SIZE:
                resultado = _process_batch(batch_rows, responsavel_lookup, criado_por)
                total_criados += resultado['created']
                total_atualizados += resultado['updated']
                total_erros += resultado['errors']
                error_lines.extend(resultado['error_lines'])
                batch_rows = []

        if batch_rows:
            resultado = _process_batch(batch_rows, responsavel_lookup, criado_por)
            total_criados += resultado['created']
            total_atualizados += resultado['updated']
            total_erros += resultado['errors']
            error_lines.extend(resultado['error_lines'])

        lote.total_itens = total_linhas
        lote.itens_importados = total_criados + total_atualizados
        lote.itens_com_erro = total_erros
        lote.status = 'concluido'
        lote.log_erros = '\n'.join(error_lines[:MAX_ERROR_LINES])
        lote.save(update_fields=['total_itens', 'itens_importados', 'itens_com_erro', 'status', 'log_erros'])
    except Exception as exc:
        lote.total_itens = total_linhas
        lote.status = 'erro'
        lote.log_erros = str(exc)
        lote.save(update_fields=['total_itens', 'status', 'log_erros'])
        raise

    return {
        'lote': lote,
        'total_linhas': total_linhas,
        'criados': total_criados,
        'atualizados': total_atualizados,
        'erros': total_erros,
        'mensagens_erro': error_lines[:MAX_ERROR_LINES],
    }
