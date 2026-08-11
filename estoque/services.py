import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from equipamentos.models import MovimentacaoEquipamento, StatusEquipamento
from equipamentos.services import aplicar_movimentacao_equipamento

from .models import ReservaEstoque, StatusReservaEstoque


def _normalizar_texto(value):
    text = str(value or '').strip().lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'\s+', ' ', text)


def _textos_iguais(left, right):
    left = _normalizar_texto(left)
    right = _normalizar_texto(right)
    return bool(left and right and left == right)


def _usuario_destino_chamado(chamado):
    return chamado.destinatario or chamado.solicitante


def _score_minimo_reserva_inteligente():
    return max(0, min(100, int(getattr(settings, 'ITAM_RESERVA_INTELIGENTE_SCORE_MINIMO', 70) or 70)))


def _itens_pendentes_reserva(chamado):
    from equipamentos.models import TipoEquipamento

    item_ids_reservados = set(
        ReservaEstoque.objects.filter(
            chamado=chamado,
            status__in=[StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA],
            item_solicitado__isnull=False,
        ).values_list('item_solicitado_id', flat=True)
    )
    itens = chamado.itens_solicitados.select_related('equipamento_entregue').order_by('id')
    return [
        item
        for item in itens
        if item.tipo_equipamento != TipoEquipamento.OUTRO
        and not item.equipamento_entregue_id
        and item.pk not in item_ids_reservados
    ]


def pontuar_equipamento_para_chamado(equipamento, chamado):
    destino = _usuario_destino_chamado(chamado)
    score = float(equipamento.score_saude or 0)
    motivos = [f'Saude {score:.0f}']

    if destino:
        if _textos_iguais(equipamento.site, destino.site):
            score += 20
            motivos.append('mesmo site')
        if _textos_iguais(equipamento.setor, destino.setor):
            score += 8
            motivos.append('mesmo setor')
        if _textos_iguais(equipamento.andar_sala, destino.andar_sala):
            score += 4
            motivos.append('mesmo andar/sala')

    if equipamento.em_garantia:
        score += 5
        motivos.append('em garantia')

    manutencoes = getattr(equipamento, 'manutencoes_total', None)
    if manutencoes is None:
        manutencoes = equipamento.total_manutencoes
    if manutencoes:
        score -= min(manutencoes, 5) * 3
        motivos.append(f'{manutencoes} manutencao(oes)')

    return round(score, 2), motivos


def _candidatos_reserva_inteligente(*, item, chamado, excluir_ids=None):
    from equipamentos.models import Equipamento, StatusEquipamento

    excluir_ids = set(excluir_ids or [])
    score_minimo = _score_minimo_reserva_inteligente()
    qs = (
        Equipamento.objects.filter(
            status=StatusEquipamento.EM_ESTOQUE,
            tipo=item.tipo_equipamento,
            score_saude__gte=score_minimo,
        )
        .exclude(pk__in=excluir_ids)
        .annotate(manutencoes_total=Count('movimentacoes', filter=Q(movimentacoes__tipo='manutencao')))
    )
    candidatos = []
    for equipamento in qs:
        score, motivos = pontuar_equipamento_para_chamado(equipamento, chamado)
        candidatos.append(
            {
                'equipamento': equipamento,
                'score': score,
                'motivos': motivos,
            }
        )

    return sorted(
        candidatos,
        key=lambda item_score: (
            -item_score['score'],
            -float(item_score['equipamento'].score_saude or 0),
            item_score['equipamento'].id_patrimonio,
        ),
    )


def sugerir_reservas_inteligentes(chamado, *, limite_por_item=3):
    pendentes = _itens_pendentes_reserva(chamado)
    usados = set()
    sugestoes = []
    bloqueios = []

    for item in pendentes:
        candidatos = _candidatos_reserva_inteligente(item=item, chamado=chamado, excluir_ids=usados)
        if not candidatos:
            bloqueios.append(
                {
                    'item': item,
                    'mensagem': (
                        f'Nenhum {item.tipo_display} em estoque com saude '
                        f'>= {_score_minimo_reserva_inteligente()} encontrado.'
                    ),
                }
            )
            continue

        recomendacao = candidatos[0]
        usados.add(recomendacao['equipamento'].pk)
        sugestoes.append(
            {
                'item': item,
                'equipamento': recomendacao['equipamento'],
                'score': recomendacao['score'],
                'motivos': recomendacao['motivos'],
                'candidatos': candidatos[:limite_por_item],
            }
        )

    return {
        'chamado': chamado,
        'score_minimo': _score_minimo_reserva_inteligente(),
        'itens_pendentes': pendentes,
        'total_pendentes': len(pendentes),
        'total_sugeridos': len(sugestoes),
        'sugestoes': sugestoes,
        'bloqueios': bloqueios,
    }


@transaction.atomic
def criar_reservas_inteligentes(*, chamado, solicitante, observacoes=''):
    from chamados.models import Chamado

    Chamado.objects.select_for_update().get(pk=chamado.pk)
    chamado = (
        Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel')
        .prefetch_related('itens_solicitados__equipamento_entregue')
        .get(pk=chamado.pk)
    )
    plano = sugerir_reservas_inteligentes(chamado)
    if not plano['itens_pendentes']:
        raise ValidationError('Este chamado nao possui itens pendentes para reserva inteligente.')
    if not plano['sugestoes']:
        mensagem = plano['bloqueios'][0]['mensagem'] if plano['bloqueios'] else 'Nenhum equipamento compativel encontrado.'
        raise ValidationError(mensagem)

    reservas = []
    observacao_base = (observacoes or 'Reserva inteligente automatica.').strip()
    for sugestao in plano['sugestoes']:
        motivos = ', '.join(sugestao['motivos'])
        observacao = (
            f'{observacao_base}\n'
            f'Sugestao automatica para {sugestao["item"].tipo_display}: '
            f'score {sugestao["score"]:.0f} ({motivos}).'
        )
        reservas.append(
            criar_reserva_estoque(
                chamado=chamado,
                item_solicitado=sugestao['item'],
                equipamento=sugestao['equipamento'],
                solicitante=solicitante,
                observacoes=observacao,
            )
        )

    return {
        'reservas': reservas,
        'plano': plano,
        'bloqueios': plano['bloqueios'],
    }


@transaction.atomic
def criar_reserva_estoque(*, chamado, equipamento, solicitante, item_solicitado=None, observacoes=''):
    if equipamento.status != StatusEquipamento.EM_ESTOQUE:
        raise ValidationError('O equipamento precisa estar em estoque para ser reservado.')

    if ReservaEstoque.objects.filter(
        equipamento=equipamento,
        status__in=[StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA],
    ).exists():
        raise ValidationError('Este equipamento já está reservado.')

    if item_solicitado and item_solicitado.chamado_id != chamado.pk:
        raise ValidationError('O item informado não pertence ao chamado selecionado.')

    reserva = ReservaEstoque.objects.create(
        chamado=chamado,
        item_solicitado=item_solicitado,
        equipamento=equipamento,
        solicitante=solicitante,
        observacoes=observacoes or '',
        status=StatusReservaEstoque.RESERVADA,
    )
    movimentacao = MovimentacaoEquipamento.objects.create(
        equipamento=equipamento,
        tipo='reserva',
        descricao=f'Reserva de estoque vinculada ao chamado #{chamado.pk} - {chamado.titulo}',
        realizado_por=solicitante,
        chamado=chamado,
        observacoes=observacoes or '',
    )
    aplicar_movimentacao_equipamento(equipamento, movimentacao)
    return reserva


@transaction.atomic
def criar_reservas_estoque_lote(*, chamado, equipamentos, solicitante, observacoes=''):
    equipamentos = list(equipamentos or [])
    if not equipamentos:
        raise ValidationError('Selecione pelo menos um equipamento para reservar em lote.')

    return [
        criar_reserva_estoque(
            chamado=chamado,
            equipamento=equipamento,
            solicitante=solicitante,
            item_solicitado=None,
            observacoes=observacoes or '',
        )
        for equipamento in equipamentos
    ]


@transaction.atomic
def liberar_reserva_estoque(*, reserva, usuario=None, motivo=''):
    if reserva.status not in {StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA}:
        raise ValidationError('Esta reserva não pode ser liberada.')

    movimentacao = MovimentacaoEquipamento.objects.create(
        equipamento=reserva.equipamento,
        tipo='liberacao_reserva',
        descricao=f'Liberação de reserva vinculada ao chamado #{reserva.chamado.pk} - {reserva.chamado.titulo}',
        realizado_por=usuario,
        chamado=reserva.chamado,
        observacoes=motivo or reserva.observacoes or '',
    )
    aplicar_movimentacao_equipamento(reserva.equipamento, movimentacao)
    reserva.cancelar(usuario=usuario, motivo=motivo)
    return reserva


@transaction.atomic
def marcar_reserva_separada(*, reserva, usuario=None):
    if reserva.status != StatusReservaEstoque.RESERVADA:
        raise ValidationError('Esta reserva já foi processada.')

    reserva.marcar_separada(usuario=usuario)
    return reserva


@transaction.atomic
def marcar_reserva_entregue(*, reserva, usuario=None):
    if reserva.status == StatusReservaEstoque.ENTREGUE:
        return reserva
    reserva.marcar_entregue(usuario=usuario)
    return reserva
