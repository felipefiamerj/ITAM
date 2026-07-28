from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone

from equipamentos.models import Equipamento, StatusEquipamento
from itam.api_auth import api_auth_required

from .forms import ReservaEstoqueForm
from .models import (
    ReservaEstoque,
    StatusReservaEstoque,
    lotes_recentes,
    reservas_ativas_queryset,
    resumo_por_localizacao,
    resumo_por_site,
    resumo_por_status,
)
from .services import criar_reserva_estoque, liberar_reserva_estoque, marcar_reserva_separada


def _status_label(status_value):
    for value, label in StatusEquipamento.choices:
        if value == status_value:
            return label
    return status_value


def _serialize_lote(lote):
    return {
        'id': lote.pk,
        'descricao': lote.descricao or lote.arquivo.name,
        'status': lote.status,
        'status_display': lote.get_status_display(),
        'total_itens': lote.total_itens,
        'itens_importados': lote.itens_importados,
        'itens_com_erro': lote.itens_com_erro,
        'created_at': timezone.localtime(lote.created_at).strftime('%d/%m/%Y %H:%M'),
    }


def _serialize_equipment(equipamento):
    return {
        'pk': equipamento.pk,
        'id_patrimonio': equipamento.id_patrimonio,
        'tipo_display': equipamento.tipo_display,
        'status': equipamento.status,
        'status_display': equipamento.get_status_display(),
        'score_saude': equipamento.score_saude,
        'localizacao': equipamento.localizacao_resumida,
    }


def _serialize_reserva(reserva):
    return {
        'id': reserva.pk,
        'status': reserva.status,
        'status_display': reserva.get_status_display(),
        'observacoes': reserva.observacoes,
        'is_ativa': reserva.is_ativa,
        'reserved_at': timezone.localtime(reserva.reserved_at).strftime('%d/%m/%Y %H:%M') if reserva.reserved_at else None,
        'separated_at': timezone.localtime(reserva.separated_at).strftime('%d/%m/%Y %H:%M') if reserva.separated_at else None,
        'delivered_at': timezone.localtime(reserva.delivered_at).strftime('%d/%m/%Y %H:%M') if reserva.delivered_at else None,
        'canceled_at': timezone.localtime(reserva.canceled_at).strftime('%d/%m/%Y %H:%M') if reserva.canceled_at else None,
        'chamado': {
            'id': reserva.chamado_id,
            'titulo': reserva.chamado.titulo if reserva.chamado_id else None,
            'status': reserva.chamado.status if reserva.chamado_id else None,
            'destinatario': reserva.chamado.destinatario_nome_completo if reserva.chamado_id else None,
        },
        'item_solicitado': {
            'id': reserva.item_solicitado_id,
            'tipo_display': reserva.item_solicitado.tipo_display if reserva.item_solicitado else None,
            'quantidade': reserva.item_solicitado.quantidade if reserva.item_solicitado else None,
        },
        'equipamento': _serialize_equipment(reserva.equipamento),
        'solicitante': reserva.solicitante.nome_completo if reserva.solicitante else None,
        'separado_por': reserva.separado_por.nome_completo if reserva.separado_por else None,
    }


@api_auth_required
def estoque_resumo_api(request):
    now = timezone.localtime(timezone.now())
    total_equipamentos = Equipamento.objects.count()
    por_status = list(resumo_por_status())
    por_site = list(resumo_por_site())
    por_localizacao = list(resumo_por_localizacao())
    lotes = list(lotes_recentes())
    reservas_ativas = list(reservas_ativas_queryset()[:20])
    alertas = list(
        Equipamento.objects.filter(score_saude__lt=70)
        .select_related('responsavel')
        .order_by('score_saude', 'id_patrimonio')[:12]
    )

    return JsonResponse(
        {
            'updated_at': now.strftime('%d/%m/%Y %H:%M:%S'),
            'updated_at_iso': now.isoformat(),
            'updated_at_display': now.strftime('%d/%m/%Y às %H:%M'),
            'totais': {
                'total_equipamentos': total_equipamentos,
                'em_uso': Equipamento.objects.filter(status=StatusEquipamento.EM_USO).count(),
                'em_estoque': Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE).count(),
                'em_manutencao': Equipamento.objects.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
                'reservados': Equipamento.objects.filter(status=StatusEquipamento.RESERVADO).count(),
                'descartados': Equipamento.objects.filter(status=StatusEquipamento.DESCARTADO).count(),
                'aguardando': Equipamento.objects.filter(status=StatusEquipamento.AGUARDANDO_APROVACAO).count(),
                'alertas': Equipamento.objects.filter(score_saude__lt=70).count(),
                'reservas_ativas': ReservaEstoque.objects.filter(
                    status__in=[StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA]
                ).count(),
            },
            'por_status': [
                {'status': item['status'], 'label': _status_label(item['status']), 'total': item['total']}
                for item in por_status
            ],
            'por_site': [
                {
                    'site': item['site'] or 'Sem site',
                    'total': item['total'],
                    'em_uso': item['em_uso'],
                    'em_estoque': item['em_estoque'],
                    'em_manutencao': item['em_manutencao'],
                    'descartado': item['descartado'],
                }
                for item in por_site
            ],
            'por_localizacao': [
                {
                    'site': item['site'] or 'Sem site',
                    'setor': item['setor'] or 'Sem setor',
                    'andar_sala': item['andar_sala'] or 'Sem andar/sala',
                    'label': ' · '.join(
                        [parte for parte in [item['site'], item['setor'], item['andar_sala']] if parte]
                    ),
                    'total': item['total'],
                    'em_uso': item['em_uso'],
                    'em_estoque': item['em_estoque'],
                    'em_manutencao': item['em_manutencao'],
                    'descartado': item['descartado'],
                }
                for item in por_localizacao
            ],
            'reservas_ativas': [_serialize_reserva(reserva) for reserva in reservas_ativas],
            'lotes': [_serialize_lote(lote) for lote in lotes],
            'alertas': [_serialize_equipment(equipamento) for equipamento in alertas],
        }
    )


@api_auth_required
def reservas_api(request):
    if not request.user.is_operacional:
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)

    if request.method == 'POST':
        form = ReservaEstoqueForm(request.POST)
        if form.is_valid():
            try:
                reserva = criar_reserva_estoque(
                    chamado=form.cleaned_data['chamado'],
                    item_solicitado=form.cleaned_data.get('item_solicitado'),
                    equipamento=form.cleaned_data['equipamento'],
                    solicitante=request.user,
                    observacoes=form.cleaned_data.get('observacoes', ''),
                )
            except ValidationError as exc:
                return JsonResponse({'detail': exc.messages[0] if exc.messages else 'Não foi possível reservar.'}, status=400)
            return JsonResponse(_serialize_reserva(reserva), status=201)

        return JsonResponse({'errors': form.errors}, status=400)

    reservas = reservas_ativas_queryset()
    return JsonResponse(
        {
            'count': reservas.count(),
            'results': [_serialize_reserva(reserva) for reserva in reservas[:50]],
        }
    )


@api_auth_required
def reserva_acao_api(request, pk):
    if not request.user.is_operacional:
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'detail': 'Método não permitido.'}, status=405)

    reserva = get_object_or_404(ReservaEstoque.objects.select_related('chamado', 'item_solicitado', 'equipamento', 'solicitante', 'separado_por'), pk=pk)
    acao = (request.POST.get('acao') or '').strip()

    try:
        if acao == 'separar':
            marcar_reserva_separada(reserva=reserva, usuario=request.user)
        elif acao == 'liberar':
            liberar_reserva_estoque(reserva=reserva, usuario=request.user, motivo=request.POST.get('motivo', ''))
        else:
            return JsonResponse({'detail': 'Ação inválida.'}, status=400)
    except ValidationError as exc:
        return JsonResponse({'detail': exc.messages[0] if exc.messages else 'Não foi possível atualizar a reserva.'}, status=400)

    reserva.refresh_from_db()
    return JsonResponse(_serialize_reserva(reserva))


urlpatterns = [
    path('resumo/', estoque_resumo_api, name='api_estoque_resumo'),
    path('reservas/', reservas_api, name='api_reservas_estoque'),
    path('reservas/<int:pk>/acao/', reserva_acao_api, name='api_reserva_estoque_acao'),
]
