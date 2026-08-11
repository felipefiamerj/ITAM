from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone

from estoque.models import reservas_ativas_por_chamado
from itam.api_auth import api_auth_required

from .models import Chamado
from .policies import (
    PAINEL_OPERACIONAL_LANES,
    pode_visualizar_chamado,
)
from .policies import (
    acoes_fluxo_chamado as _fluxo_acoes,
)
from .policies import (
    pode_gerenciar_chamado as _pode_gerenciar,
)


def _serialize_item(item):
    return {
        'id': item.pk,
        'tipo_equipamento': item.tipo_equipamento,
        'tipo_display': item.tipo_display,
        'tipo_outro': item.tipo_outro,
        'quantidade': item.quantidade,
        'observacao': item.observacao,
        'equipamento_entregue': item.equipamento_entregue.id_patrimonio if item.equipamento_entregue else None,
        'entregue_por': item.entregue_por.nome_completo if item.entregue_por else None,
        'entregue_em': timezone.localtime(item.entregue_em).strftime('%d/%m/%Y %H:%M') if item.entregue_em else None,
    }


def _serialize_chamado(chamado):
    reservas = reservas_ativas_por_chamado(chamado)
    return {
        'id': chamado.pk,
        'titulo': chamado.titulo,
        'descricao': chamado.descricao,
        'servico_realizado': chamado.servico_realizado,
        'servico_realizado_display': chamado.get_servico_realizado_display() if chamado.servico_realizado else None,
        'tipo_equipamento_solicitado': chamado.tipo_equipamento_solicitado,
        'tipo_equipamento_solicitado_display': chamado.get_tipo_equipamento_solicitado_display()
        if chamado.tipo_equipamento_solicitado
        else None,
        'status': chamado.status,
        'status_display': chamado.get_status_display(),
        'fluxo_etapa': chamado.fluxo_etapa,
        'fluxo_etapa_label': chamado.fluxo_etapa_label,
        'fluxo_etapa_descricao': chamado.fluxo_etapa_descricao,
        'prioridade': chamado.prioridade,
        'prioridade_display': chamado.get_prioridade_display(),
        'solicitante': chamado.solicitante.nome_completo,
        'destinatario': chamado.destinatario_nome_completo,
        'responsavel': chamado.responsavel.nome_completo if chamado.responsavel else None,
        'equipamento': chamado.equipamento.id_patrimonio if chamado.equipamento else None,
        'solucao': chamado.solucao,
        'data_fechamento': timezone.localtime(chamado.data_fechamento).strftime('%d/%m/%Y %H:%M')
        if chamado.data_fechamento
        else None,
        'created_at': timezone.localtime(chamado.created_at).strftime('%d/%m/%Y %H:%M'),
        'updated_at': timezone.localtime(chamado.updated_at).strftime('%d/%m/%Y %H:%M'),
        'itens_solicitados': [_serialize_item(item) for item in chamado.itens_solicitados.all().order_by('id')],
        'reservas_ativas': [
            {
                'id': reserva.pk,
                'equipamento': reserva.equipamento.id_patrimonio,
                'status': reserva.status,
                'status_display': reserva.get_status_display(),
                'item_solicitado': reserva.item_solicitado.tipo_display if reserva.item_solicitado else None,
            }
            for reserva in reservas
        ],
    }


@api_auth_required
def chamados_api(request):
    qs = Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por').prefetch_related(
        'itens_solicitados',
    )
    if not _pode_gerenciar(request.user):
        qs = qs.filter(Q(solicitante=request.user) | Q(destinatario=request.user))

    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    if q:
        qs = qs.filter(
            Q(titulo__icontains=q)
            | Q(descricao__icontains=q)
            | Q(servico_realizado__icontains=q)
            | Q(tipo_equipamento_solicitado__icontains=q)
            | Q(equipamento__id_patrimonio__icontains=q)
            | Q(solicitante__matricula__icontains=q)
            | Q(destinatario__matricula__icontains=q)
            | Q(responsavel__matricula__icontains=q)
        ).distinct()
    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by('-updated_at', '-created_at')
    return JsonResponse({'count': qs.count(), 'results': [_serialize_chamado(chamado) for chamado in qs[:50]]})


@api_auth_required
def chamado_api(request, pk):
    chamado = get_object_or_404(
        Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por').prefetch_related(
            'itens_solicitados__equipamento_entregue',
            'itens_solicitados__entregue_por',
        ),
        pk=pk,
    )
    if not pode_visualizar_chamado(request.user, chamado):
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)
    return JsonResponse(_serialize_chamado(chamado))


@api_auth_required
def painel_tecnico_api(request):
    if not _pode_gerenciar(request.user):
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)

    qs = Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por').prefetch_related(
        'itens_solicitados',
    )
    results = []
    for lane in PAINEL_OPERACIONAL_LANES:
        lane_qs = qs.filter(fluxo_etapa__in=lane['etapas']).order_by('-updated_at', '-created_at')
        results.append(
            {
                'key': lane['key'],
                'label': lane['label'],
                'count': lane_qs.count(),
                'items': [
                    {
                        'chamado': _serialize_chamado(chamado),
                        'acoes': _fluxo_acoes(request.user, chamado),
                    }
                    for chamado in lane_qs[:6]
                ],
            }
        )

    return JsonResponse({'results': results})


urlpatterns = [
    path('', chamados_api, name='api_chamados'),
    path('<int:pk>/', chamado_api, name='api_chamado'),
    path('painel/', painel_tecnico_api, name='api_painel_tecnico'),
]
