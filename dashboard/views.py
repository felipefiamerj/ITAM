from auditlog.models import LogEntry
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render

from accounts.models import Usuario
from chamados.models import Chamado, PrioridadeChamado, StatusChamado
from equipamentos.models import Equipamento, StatusEquipamento
from notifications.models import Notification
from itam.charting import build_choice_chart
from .search import build_search_payload


FLUXO_CHAMADO_DASHBOARD = [
    {
        'status': StatusChamado.FILA,
        'label': 'Fila',
        'description': 'Chamados aguardando triagem ou assunÃ§Ã£o pelo time.',
    },
    {
        'status': StatusChamado.EM_ATENDIMENTO,
        'label': 'Em atendimento',
        'description': 'Chamados que jÃ¡ estÃ£o com um responsÃ¡vel definido.',
    },
    {
        'status': StatusChamado.AGUARDANDO_ATENDIMENTO,
        'label': 'Aguardando atendimento',
        'description': 'Chamados pausados para retorno, peÃ§a ou prÃ³xima aÃ§Ã£o.',
    },
    {
        'status': StatusChamado.ENCERRADO,
        'label': 'Encerrado',
        'description': 'Chamados finalizados e prontos para consulta.',
    },
]


def _chamados_fluxo_dashboard(chamados):
    contagem_por_status = {
        item['status']: item['total']
        for item in chamados.values('status').annotate(total=Count('id'))
    }
    recentes = list(chamados.select_related('solicitante', 'destinatario', 'responsavel').order_by('-updated_at', '-created_at')[:40])

    fluxo = []
    for etapa in FLUXO_CHAMADO_DASHBOARD:
        status = etapa['status']
        fluxo.append(
            {
                'status': status,
                'label': etapa['label'],
                'description': etapa['description'],
                'count': contagem_por_status.get(status, 0),
                'items': [chamado for chamado in recentes if chamado.status == status][:5],
            }
        )

    return fluxo


@login_required
def dashboard_view(request):
    if request.user.is_solicitante:
        return redirect('chamados')
    equipamentos = Equipamento.objects.select_related('responsavel', 'criado_por')
    chamados = Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel')

    chamados_abertos = chamados.exclude(status=StatusChamado.ENCERRADO)
    chamados_criticos = chamados_abertos.filter(prioridade=PrioridadeChamado.CRITICA)
    equipamentos_por_status = list(
        equipamentos.values('status')
        .annotate(total=Count('id'))
        .order_by('status')
    )
    chamados_abertos_por_prioridade = list(
        chamados_abertos.values('prioridade')
        .annotate(total=Count('id'))
        .order_by('prioridade')
    )

    context = {
        'total_equipamentos': equipamentos.count(),
        'equipamentos_em_uso': equipamentos.filter(status=StatusEquipamento.EM_USO).count(),
        'equipamentos_em_estoque': equipamentos.filter(status=StatusEquipamento.EM_ESTOQUE).count(),
        'equipamentos_em_manutencao': equipamentos.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
        'equipamentos_alerta': equipamentos.filter(score_saude__lt=70).count(),
        'chamados_abertos': chamados_abertos.count(),
        'chamados_criticos': chamados_criticos.count(),
        'usuarios_pendentes': Usuario.objects.filter(solicitacao_pendente=True).count(),
        'usuarios_ativos': Usuario.objects.filter(ativo=True, solicitacao_pendente=False).count(),
        'usuarios_pendentes_recentes': Usuario.objects.filter(solicitacao_pendente=True)
        .select_related('gestor')
        .order_by('created_at')[:5],
        'atividade_recente': LogEntry.objects.filter(
            content_type__app_label__in=['accounts', 'chamados', 'equipamentos']
        )
        .select_related('actor', 'content_type')
        .order_by('-timestamp')[:8],
        'chamados_recentes': chamados.order_by('-created_at')[:5],
        'equipamentos_recentes': equipamentos.order_by('-created_at')[:5],
        'notificacoes_nao_lidas': Notification.objects.filter(user=request.user, is_read=False).count(),
        'dashboard_charts': {
            'equipamentos_por_status': build_choice_chart(
                equipamentos_por_status,
                StatusEquipamento.choices,
                'status',
            ),
            'chamados_abertos_por_prioridade': build_choice_chart(
                chamados_abertos_por_prioridade,
                PrioridadeChamado.choices,
                'prioridade',
            ),
        },
    }

    if request.user.is_operacional:
        context['chamados_fluxo'] = _chamados_fluxo_dashboard(chamados)

    return render(request, 'dashboard/index.html', context)


@login_required
def busca_view(request):
    payload = build_search_payload(request.user, request.GET.get('q', ''))
    return render(request, 'dashboard/busca.html', payload)


@login_required
def busca_global_api(request):
    return JsonResponse(build_search_payload(request.user, request.GET.get('q', '')))


