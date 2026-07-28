import json
import re
import unicodedata
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from estoque.models import reservas_ativas_por_chamado
from estoque.services import marcar_reserva_entregue
from equipamentos.models import Equipamento, MovimentacaoEquipamento, StatusEquipamento, TipoEquipamento
from equipamentos.services import aplicar_movimentacao_equipamento
from notifications.services import notificar_time_operacional, notificar_usuarios

from .models import Chamado, ChamadoItemSolicitado, EtapaFluxoChamado, PrioridadeChamado, SLANivel, StatusChamado


def _normalizar_texto(texto):
    texto = (texto or '').strip().lower()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(char for char in texto if not unicodedata.combining(char))
    texto = re.sub(r'[^a-z0-9]+', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()


def _montar_lookup_tipos():
    lookup = {}
    for value, label in TipoEquipamento.choices:
        lookup[_normalizar_texto(value)] = value
        lookup[_normalizar_texto(label)] = value
    return lookup


_TIPO_EQUIPAMENTO_LOOKUP = _montar_lookup_tipos()


def _usuarios_afetados_sla(chamado):
    usuarios = [chamado.solicitante, chamado.destinatario, chamado.responsavel]
    vistos = set()
    resultado = []

    for usuario in usuarios:
        if not usuario or usuario.pk in vistos:
            continue
        vistos.add(usuario.pk)
        resultado.append(usuario)

    return resultado


def _usuarios_externos_sla(chamado):
    return [usuario for usuario in _usuarios_afetados_sla(chamado) if not usuario.is_operacional]


def calcular_prazo_sla(chamado, base=None):
    base = base or chamado.created_at or timezone.now()
    minutos = chamado.sla_duracao_minutos
    return base + timedelta(minutes=minutos)


def calcular_momento_alerta_sla(chamado, base=None):
    prazo = calcular_prazo_sla(chamado, base=base)
    janela = max(30, int(chamado.sla_duracao_minutos * 0.25))
    return prazo - timedelta(minutes=janela)


def avaliar_sla_chamado(chamado, now=None):
    now = now or timezone.now()
    prazo = calcular_prazo_sla(chamado)
    alerta = calcular_momento_alerta_sla(chamado)

    if chamado.status == StatusChamado.ENCERRADO:
        estado = 'encerrado'
    elif chamado.sla_nivel == SLANivel.ESCALADO or now >= prazo:
        estado = SLANivel.ESCALADO
    elif chamado.sla_nivel == SLANivel.ALERTA or now >= alerta:
        estado = SLANivel.ALERTA
    else:
        estado = SLANivel.NORMAL

    return {
        'estado': estado,
        'prazo_em': prazo,
        'alerta_em': alerta,
        'em_atraso': now >= prazo,
        'minutos_restantes': int((prazo - now).total_seconds() // 60),
    }


def _notificar_sla(chamado, *, nivel, now):
    link = reverse('detalhe_chamado', kwargs={'pk': chamado.pk})
    if nivel == SLANivel.ALERTA:
        titulo = f'SLA em atenção: chamado #{chamado.pk}'
        mensagem = (
            f'O chamado #{chamado.pk} - {chamado.titulo} entrou na janela de atenção do SLA. '
            f'Prazo estimado: {chamado.sla_prazo_em.strftime("%d/%m/%Y %H:%M")}.'
        )
    else:
        titulo = f'SLA escalonado: chamado #{chamado.pk}'
        mensagem = (
            f'O chamado #{chamado.pk} - {chamado.titulo} ultrapassou o SLA em '
            f'{max(1, int((now - chamado.sla_prazo_em).total_seconds() // 60))} minuto(s).'
        )

    externos = _usuarios_externos_sla(chamado)
    if externos:
        notificar_usuarios(externos, titulo, mensagem, link=link)
    notificar_time_operacional(titulo, mensagem, link=link)


def verificar_sla_chamados(now=None):
    now = now or timezone.now()
    chamados = (
        Chamado.objects.exclude(status=StatusChamado.ENCERRADO)
        .select_related('solicitante', 'destinatario', 'responsavel')
        .order_by('created_at')
    )

    alertados = 0
    escalados = 0

    for chamado in chamados.iterator():
        avaliacao = avaliar_sla_chamado(chamado, now=now)
        estado = avaliacao['estado']

        if estado == SLANivel.ESCALADO and chamado.sla_nivel != SLANivel.ESCALADO:
            chamado.sla_nivel = SLANivel.ESCALADO
            if not chamado.sla_alertado_em:
                chamado.sla_alertado_em = now
            chamado.sla_escalado_em = now
            if chamado.prioridade != PrioridadeChamado.CRITICA:
                chamado.prioridade = PrioridadeChamado.CRITICA
                update_fields = ['sla_nivel', 'sla_alertado_em', 'sla_escalado_em', 'prioridade', 'updated_at']
            else:
                update_fields = ['sla_nivel', 'sla_alertado_em', 'sla_escalado_em', 'updated_at']
            chamado.save(update_fields=update_fields)
            _notificar_sla(chamado, nivel=SLANivel.ESCALADO, now=now)
            escalados += 1
            continue

        if estado == SLANivel.ALERTA and chamado.sla_nivel == SLANivel.NORMAL:
            chamado.sla_nivel = SLANivel.ALERTA
            if not chamado.sla_alertado_em:
                chamado.sla_alertado_em = now
            chamado.save(update_fields=['sla_nivel', 'sla_alertado_em', 'updated_at'])
            _notificar_sla(chamado, nivel=SLANivel.ALERTA, now=now)
            alertados += 1

    return {
        'alertados': alertados,
        'escalados': escalados,
    }


def _resolver_tipo_item(texto):
    chave = _normalizar_texto(texto)
    if not chave:
        return None, ''

    tipo_equipamento = _TIPO_EQUIPAMENTO_LOOKUP.get(chave)
    if tipo_equipamento:
        return tipo_equipamento, ''

    return TipoEquipamento.OUTRO, (texto or '').strip()


def _parse_linha_item(texto):
    linha = (texto or '').strip()
    if not linha:
        return None

    linha = linha.lstrip('-*•').strip()
    if not linha:
        return None

    quantidade = 1
    observacao = ''
    tipo_texto = linha

    padrao = re.match(
        r'^(?P<tipo>.+?)\s*[xX*]\s*(?P<quantidade>\d+)\s*(?:[;,-]\s*(?P<obs>.*))?$',
        linha,
    )
    if padrao:
        tipo_texto = padrao.group('tipo').strip()
        quantidade = int(padrao.group('quantidade'))
        observacao = (padrao.group('obs') or '').strip()
    elif ';' in linha:
        partes = [parte.strip() for parte in linha.split(';')]
        tipo_texto = partes[0]
        if len(partes) > 1:
            if partes[1].isdigit():
                quantidade = int(partes[1])
                observacao = '; '.join(parte for parte in partes[2:] if parte)
            else:
                observacao = '; '.join(parte for parte in partes[1:] if parte)
    elif ' - ' in linha:
        partes = [parte.strip() for parte in linha.split(' - ', 2)]
        tipo_texto = partes[0]
        if len(partes) > 1 and partes[1].isdigit():
            quantidade = int(partes[1])
            observacao = partes[2] if len(partes) > 2 else ''
        else:
            observacao = ' - '.join(parte for parte in partes[1:] if parte)

    if quantidade < 1:
        raise ValidationError('A quantidade precisa ser maior que zero.')

    tipo_equipamento, tipo_outro = _resolver_tipo_item(tipo_texto)
    if not tipo_equipamento:
        raise ValidationError('Informe o tipo do item solicitado.')

    return {
        'tipo_equipamento': tipo_equipamento,
        'tipo_outro': tipo_outro,
        'quantidade': quantidade,
        'observacao': observacao,
    }


def _parse_itens_solicitados(texto):
    itens = []
    erros = []
    for indice, linha in enumerate((texto or '').splitlines(), start=1):
        linha = linha.strip()
        if not linha:
            continue
        try:
            item = _parse_linha_item(linha)
        except ValidationError as exc:
            erros.append(f'Linha {indice}: {exc.messages[0] if exc.messages else "Item inválido."}')
            continue
        if item:
            itens.append(item)
    return itens, erros


def _normalizar_selecoes_entrega(selecoes):
    if selecoes in (None, '', {}):
        return {}

    if isinstance(selecoes, str):
        try:
            selecoes = json.loads(selecoes)
        except json.JSONDecodeError as exc:
            raise ValidationError('Seleções de equipamentos inválidas.') from exc

    if not isinstance(selecoes, dict):
        raise ValidationError('Seleções de equipamentos inválidas.')

    normalizadas = {}
    for item_id_raw, equipamento_id_raw in selecoes.items():
        try:
            item_id = int(item_id_raw)
            equipamento_id = int(equipamento_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError('Seleções de equipamentos inválidas.') from exc

        normalizadas[item_id] = equipamento_id

    return normalizadas


@transaction.atomic
def registrar_entrega_chamado(*, chamado, equipamento, realizado_por, observacoes='', concluir_chamado=True):
    reserva = reservas_ativas_por_chamado(chamado).filter(equipamento=equipamento).first()
    if equipamento.status not in {StatusEquipamento.EM_ESTOQUE, StatusEquipamento.RESERVADO}:
        raise ValidationError('Selecione um equipamento disponível em estoque.')
    if equipamento.status == StatusEquipamento.RESERVADO and not reserva:
        raise ValidationError('Este equipamento reservado não pertence a este chamado.')

    destinatario = chamado.usuario_destinatario
    descricao = f'Entrega vinculada ao chamado #{chamado.pk} - {chamado.titulo}'
    if chamado.tipo_equipamento_solicitado:
        descricao = f'{descricao} ({chamado.get_tipo_equipamento_solicitado_display()})'
    if destinatario:
        descricao = f'{descricao} - {destinatario.nome_completo}'

    movimentacao = MovimentacaoEquipamento.objects.create(
        equipamento=equipamento,
        tipo='saida',
        descricao=descricao,
        usuario_anterior=equipamento.responsavel,
        usuario_novo=destinatario,
        realizado_por=realizado_por,
        chamado=chamado,
        observacoes=observacoes or '',
    )

    aplicar_movimentacao_equipamento(equipamento, movimentacao)
    if reserva:
        marcar_reserva_entregue(reserva=reserva, usuario=realizado_por)

    chamado.equipamento = equipamento
    chamado.responsavel = realizado_por

    if concluir_chamado:
        chamado.status = StatusChamado.ENCERRADO
        chamado.fluxo_etapa = EtapaFluxoChamado.ENCERRADO
        resumo = f'Equipamento entregue: {equipamento.id_patrimonio}.'
        if observacoes:
            resumo = f'{resumo}\nObservações da entrega: {observacoes}'

        if chamado.solucao:
            if resumo not in chamado.solucao:
                chamado.solucao = f'{chamado.solucao.rstrip()}\n\n{resumo}'
        else:
            chamado.solucao = resumo
    else:
        chamado.status = StatusChamado.EM_ATENDIMENTO
        chamado.fluxo_etapa = EtapaFluxoChamado.EM_SEPARACAO

    chamado.save()
    return movimentacao


@transaction.atomic
def excluir_chamado_administrativo(*, chamado, usuario, motivo=''):
    if not getattr(usuario, 'is_admin', False):
        raise ValidationError('Somente administradores podem excluir chamados.')

    if chamado.itens_solicitados.filter(equipamento_entregue__isnull=False).exists():
        raise ValidationError('Este chamado possui itens entregues e nao pode ser excluido.')

    reservas_ativas = list(reservas_ativas_por_chamado(chamado).select_related('equipamento').order_by('id'))
    observacoes = motivo or 'Exclusao administrativa do chamado.'

    for reserva in reservas_ativas:
        movimentacao = MovimentacaoEquipamento.objects.create(
            equipamento=reserva.equipamento,
            tipo='liberacao_reserva',
            descricao=f'Liberacao administrativa antes da exclusao do chamado #{chamado.pk} - {chamado.titulo}',
            realizado_por=usuario,
            chamado=chamado,
            observacoes=observacoes,
        )
        aplicar_movimentacao_equipamento(reserva.equipamento, movimentacao)

    total_reservas = len(reservas_ativas)
    chamado.delete()
    return total_reservas


@transaction.atomic
def registrar_entregas_chamado(*, chamado, selecoes_itens, realizado_por, observacoes='', concluir_chamado=True):
    selecoes = _normalizar_selecoes_entrega(selecoes_itens)
    itens = list(
        chamado.itens_solicitados.select_related('equipamento_entregue', 'entregue_por').order_by('id')
    )

    if not itens:
        raise ValidationError('Este chamado nao possui itens solicitados.')
    if not selecoes:
        raise ValidationError('Selecione pelo menos um item para registrar a entrega.')

    destinatario = chamado.usuario_destinatario
    itens_por_id = {item.id: item for item in itens}
    equipamentos_usados = set()
    movimentos_criados = []
    agora = timezone.now()

    for item_id, equipamento_id in selecoes.items():
        item = itens_por_id.get(item_id)
        if not item:
            raise ValidationError('A selecao enviada nao corresponde aos itens deste chamado.')

        equipamento = Equipamento.objects.select_related('responsavel').filter(pk=equipamento_id).first()
        if not equipamento:
            raise ValidationError('Selecione um equipamento valido.')
        reserva = reservas_ativas_por_chamado(chamado).filter(
            item_solicitado_id=item.id,
            equipamento=equipamento,
        ).first()
        if equipamento.status not in {StatusEquipamento.EM_ESTOQUE, StatusEquipamento.RESERVADO}:
            raise ValidationError(f'O equipamento {equipamento.id_patrimonio} nao esta disponivel em estoque.')
        if equipamento.status == StatusEquipamento.RESERVADO and not reserva:
            raise ValidationError(f'O equipamento {equipamento.id_patrimonio} reservado nao pertence a este chamado.')
        if equipamento.id in equipamentos_usados:
            raise ValidationError('O mesmo equipamento nao pode ser selecionado para mais de um item.')
        equipamentos_usados.add(equipamento.id)

        if item.equipamento_entregue_id and item.equipamento_entregue_id != equipamento.id:
            raise ValidationError(f'O item {item.tipo_display} ja possui um equipamento selecionado.')

        if not item.equipamento_entregue_id:
            descricao = f'Entrega vinculada ao chamado #{chamado.pk} - {chamado.titulo} ({item.tipo_display})'
            if destinatario:
                descricao = f'{descricao} - {destinatario.nome_completo}'
            movimentacao = MovimentacaoEquipamento.objects.create(
                equipamento=equipamento,
                tipo='saida',
                descricao=descricao,
                usuario_anterior=equipamento.responsavel,
                usuario_novo=destinatario,
                realizado_por=realizado_por,
                chamado=chamado,
                observacoes=observacoes or '',
            )

            aplicar_movimentacao_equipamento(equipamento, movimentacao)
            movimentos_criados.append(movimentacao)
            if reserva:
                marcar_reserva_entregue(reserva=reserva, usuario=realizado_por)

        item.equipamento_entregue = equipamento
        item.entregue_por = realizado_por
        if not item.entregue_em:
            item.entregue_em = agora
        item.save(update_fields=['equipamento_entregue', 'entregue_por', 'entregue_em'])

    pendentes = [item for item in itens if item.tipo_equipamento != TipoEquipamento.OUTRO and not item.equipamento_entregue_id]
    if concluir_chamado and pendentes:
        raise ValidationError(
            f'Selecione um equipamento para todos os {len(pendentes)} item(ns) antes de encerrar o chamado.'
        )

    chamado.responsavel = realizado_por
    if not chamado.equipamento_id:
        primeiro_entregue = next((item.equipamento_entregue for item in itens if item.equipamento_entregue_id), None)
        if primeiro_entregue:
            chamado.equipamento = primeiro_entregue

    if concluir_chamado and not pendentes:
        chamado.status = StatusChamado.ENCERRADO
        chamado.fluxo_etapa = EtapaFluxoChamado.ENCERRADO
        linhas_resumo = [
            f'- {item.tipo_display}: {item.equipamento_entregue.id_patrimonio}'
            for item in itens
            if item.equipamento_entregue_id
        ]
        resumo = 'Itens entregues:\n' + '\n'.join(linhas_resumo)
        if observacoes:
            resumo = f'{resumo}\n\nObservacoes da entrega:\n{observacoes}'

        if chamado.solucao:
            if resumo not in chamado.solucao:
                chamado.solucao = f'{chamado.solucao.rstrip()}\n\n{resumo}'
        else:
            chamado.solucao = resumo
    else:
        chamado.status = StatusChamado.EM_ATENDIMENTO
        chamado.fluxo_etapa = EtapaFluxoChamado.EM_SEPARACAO

    chamado.save()
    return movimentos_criados


@transaction.atomic
def sincronizar_itens_solicitados(*, chamado, tipos_solicitados=None, texto_itens=''):
    itens = []
    erros = []

    tipos_solicitados = list(dict.fromkeys(tipos_solicitados or []))
    for tipo in tipos_solicitados:
        tipo_equipamento, tipo_outro = _resolver_tipo_item(tipo)
        if not tipo_equipamento:
            continue
        itens.append(
            {
                'tipo_equipamento': tipo_equipamento,
                'tipo_outro': tipo_outro,
                'quantidade': 1,
                'observacao': '',
            }
        )

    if texto_itens:
        itens_texto, erros = _parse_itens_solicitados(texto_itens)
        itens.extend(itens_texto)

    if erros:
        raise ValidationError(erros)

    objetos_existentes = list(chamado.itens_solicitados.order_by('id'))
    objetos_salvos = []

    for indice, item in enumerate(itens):
        if indice < len(objetos_existentes):
            objeto = objetos_existentes[indice]
            alterado = (
                objeto.tipo_equipamento != item['tipo_equipamento']
                or objeto.tipo_outro != item['tipo_outro']
                or objeto.quantidade != item['quantidade']
                or objeto.observacao != item['observacao']
            )
            objeto.tipo_equipamento = item['tipo_equipamento']
            objeto.tipo_outro = item['tipo_outro']
            objeto.quantidade = item['quantidade']
            objeto.observacao = item['observacao']
            if alterado:
                objeto.equipamento_entregue = None
                objeto.entregue_por = None
                objeto.entregue_em = None
                objeto.save(
                    update_fields=[
                        'tipo_equipamento',
                        'tipo_outro',
                        'quantidade',
                        'observacao',
                        'equipamento_entregue',
                        'entregue_por',
                        'entregue_em',
                    ]
                )
            else:
                objeto.save(
                    update_fields=[
                        'tipo_equipamento',
                        'tipo_outro',
                        'quantidade',
                        'observacao',
                    ]
                )
        else:
            objeto = ChamadoItemSolicitado.objects.create(
                chamado=chamado,
                tipo_equipamento=item['tipo_equipamento'],
                tipo_outro=item['tipo_outro'],
                quantidade=item['quantidade'],
                observacao=item['observacao'],
            )

        objetos_salvos.append(objeto)

    for objeto in objetos_existentes[len(itens):]:
        objeto.delete()

    if objetos_salvos:
        chamado.tipo_equipamento_solicitado = objetos_salvos[0].tipo_equipamento
    else:
        chamado.tipo_equipamento_solicitado = ''

    chamado.save(update_fields=['tipo_equipamento_solicitado', 'updated_at'])

    return objetos_salvos
