from django.core.exceptions import ValidationError
from django.db import transaction

from equipamentos.models import MovimentacaoEquipamento, StatusEquipamento
from equipamentos.services import aplicar_movimentacao_equipamento

from .models import ReservaEstoque, StatusReservaEstoque


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
