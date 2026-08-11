from auditlog.models import LogEntry
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from chamados.models import Chamado, EtapaFluxoChamado, PrioridadeChamado, SLANivel, StatusChamado
from chamados.views import painel_tecnico as painel_tecnico_view
from equipamentos.models import Equipamento, StatusEquipamento
from estoque.models import reservas_ativas_queryset
from estoque.views import estoque_view as estoque_workspace_view
from itam.charting import build_choice_chart
from notifications.models import Notification

from .search import build_search_payload

FLUXO_CHAMADO_DASHBOARD = [
    {
        'status': StatusChamado.FILA,
        'label': 'Fila',
        'description': 'Chamados aguardando triagem ou assunção pelo time.',
    },
    {
        'status': StatusChamado.EM_ATENDIMENTO,
        'label': 'Em atendimento',
        'description': 'Chamados que já estão com um responsável definido.',
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
    recentes = list(
        chamados.select_related('solicitante', 'destinatario', 'responsavel').order_by('-updated_at', '-created_at')[:40]
    )

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


def _portal_solicitante_context(request):
    chamados = Chamado.objects.filter(Q(solicitante=request.user) | Q(destinatario=request.user)).select_related(
        'equipamento',
        'solicitante',
        'destinatario',
        'responsavel',
        'aprovado_por',
    ).prefetch_related('itens_solicitados').order_by('-updated_at', '-created_at')
    chamados_abertos = chamados.exclude(status=StatusChamado.ENCERRADO)
    status_resumo = list(chamados.values('status').annotate(total=Count('id')).order_by('status'))
    equipamentos_usuario = (
        Equipamento.objects.filter(responsavel=request.user, status=StatusEquipamento.EM_USO)
        .select_related('responsavel')
        .order_by('tipo', 'id_patrimonio')
    )
    notificacoes = Notification.objects.filter(user=request.user).order_by('-created_at')

    return {
        'portal_chamados_total': chamados.count(),
        'portal_chamados_abertos': chamados_abertos.count(),
        'portal_chamados_triagem': chamados.filter(fluxo_etapa='triagem').count(),
        'portal_chamados_aguardando_aprovacao': chamados.filter(fluxo_etapa='aguardando_aprovacao').count(),
        'portal_chamados_em_andamento': chamados.filter(status=StatusChamado.EM_ATENDIMENTO).count(),
        'portal_chamados_encerrados': chamados.filter(status=StatusChamado.ENCERRADO).count(),
        'portal_chamados_pendentes_aprovacao': chamados.filter(
            fluxo_etapa='aguardando_aprovacao',
            destinatario=request.user,
        ).count(),
        'portal_equipamentos': equipamentos_usuario[:6],
        'portal_equipamentos_total': equipamentos_usuario.count(),
        'portal_notificacoes': notificacoes[:5],
        'portal_notificacoes_nao_lidas': notificacoes.filter(is_read=False).count(),
        'portal_chamados_recentes': chamados[:8],
        'dashboard_charts': {
            'equipamentos_por_status': build_choice_chart(
                status_resumo,
                StatusChamado.choices,
                'status',
            ),
        },
    }


@login_required
def dashboard_view(request):
    if request.user.is_solicitante:
        return render(request, 'dashboard/portal_solicitante.html', _portal_solicitante_context(request))

    if request.user.is_admin:
        equipamentos = Equipamento.objects.select_related('responsavel', 'criado_por')
        chamados = Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel')

        chamados_abertos = chamados.exclude(status=StatusChamado.ENCERRADO)
        chamados_criticos = chamados_abertos.filter(prioridade=PrioridadeChamado.CRITICA)
        chamados_sla_alerta = chamados_abertos.filter(sla_nivel=SLANivel.ALERTA)
        chamados_sla_escalado = chamados_abertos.filter(sla_nivel=SLANivel.ESCALADO)
        chamados_sla_risco = chamados_sla_alerta.count() + chamados_sla_escalado.count()
        usuarios_pendentes_count = Usuario.objects.filter(solicitacao_pendente=True).count()
        reservas_ativas_count = reservas_ativas_queryset().count()
        equipamentos_alerta_count = equipamentos.filter(score_saude__lt=70).count()
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

        dashboard_actions = [
            {
                'key': 'aprovações',
                'label': 'Aprovações pendentes',
                'description': 'Analise novas contas e libere o acesso do time.',
                'count': usuarios_pendentes_count,
                'url': reverse('usuarios_pendentes'),
                'icon': 'fa-user-clock',
                'tone': 'violet',
            },
            {
                'key': 'críticos',
                'label': 'Chamados críticos',
                'description': 'Abra a fila operacional e resolva o que é urgente.',
                'count': chamados_criticos.count(),
                'url': reverse('painel_tecnico'),
                'icon': 'fa-triangle-exclamation',
                'tone': 'amber',
            },
            {
                'key': 'sla',
                'label': 'SLA em risco',
                'description': 'Revise chamados em alerta ou escalonados antes que virem incidentes.',
                'count': chamados_sla_risco,
                'url': f"{reverse('chamados')}?sla=alerta",
                'icon': 'fa-stopwatch',
                'tone': 'red',
            },
            {
                'key': 'estoque',
                'label': 'Reservas ativas',
                'description': 'Conferir separações e liberar o que está parado no estoque.',
                'count': reservas_ativas_count,
                'url': reverse('estoque'),
                'icon': 'fa-warehouse',
                'tone': 'blue',
            },
            {
                'key': 'saúde',
                'label': 'Alertas de saúde',
                'description': 'Revise ativos com score baixo antes de virarem incidente.',
                'count': equipamentos_alerta_count,
                'url': reverse('equipamentos'),
                'icon': 'fa-heart-circle-exclamation',
                'tone': 'teal',
            },
        ]
        dashboard_focus_action = next((action for action in dashboard_actions if action['count']), dashboard_actions[0])
        dashboard_focus_total = sum(action['count'] for action in dashboard_actions)
        dashboard_focus_total_safe = max(dashboard_focus_total, 1)

        context = {
            'total_equipamentos': equipamentos.count(),
            'equipamentos_em_uso': equipamentos.filter(status=StatusEquipamento.EM_USO).count(),
            'equipamentos_em_estoque': equipamentos.filter(status=StatusEquipamento.EM_ESTOQUE).count(),
            'equipamentos_em_manutencao': equipamentos.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
            'equipamentos_alerta': equipamentos_alerta_count,
            'chamados_abertos': chamados_abertos.count(),
            'chamados_criticos': chamados_criticos.count(),
            'chamados_sla_alerta': chamados_sla_alerta.count(),
            'chamados_sla_escalado': chamados_sla_escalado.count(),
            'chamados_sla_risco': chamados_sla_risco,
            'usuarios_pendentes': usuarios_pendentes_count,
            'usuarios_ativos': Usuario.objects.filter(ativo=True, solicitacao_pendente=False).count(),
            'reservas_ativas': reservas_ativas_count,
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
            'dashboard_actions': dashboard_actions,
            'dashboard_focus_action': dashboard_focus_action,
            'dashboard_focus_total': dashboard_focus_total,
            'dashboard_focus_total_safe': dashboard_focus_total_safe,
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
            chamados_fluxo = _chamados_fluxo_dashboard(chamados)
            context['chamados_fluxo'] = chamados_fluxo
            context['dashboard_flow_total'] = sum(item['count'] for item in chamados_fluxo)
            context['dashboard_flow_total_safe'] = max(context['dashboard_flow_total'], 1)
            context['dashboard_charts']['fluxo_chamados'] = {
                'labels': [item['label'] for item in chamados_fluxo],
                'values': [item['count'] for item in chamados_fluxo],
            }

        return render(request, 'dashboard/index.html', context)

    if request.user.is_analista:
        return estoque_workspace_view(request)

    if request.user.is_tecnico:
        return painel_tecnico_view(request)

    return render(request, 'dashboard/index.html', {})


def build_relatorios_context():
    chamados = Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel', 'equipamento')
    chamados_abertos = chamados.exclude(status=StatusChamado.ENCERRADO)
    chamados_encerrados = chamados.filter(status=StatusChamado.ENCERRADO)
    equipamentos = Equipamento.objects.select_related('responsavel', 'criado_por')

    chamado_status_resumo = list(chamados.values('status').annotate(total=Count('id')).order_by('status'))
    chamado_prioridade_resumo = list(chamados_abertos.values('prioridade').annotate(total=Count('id')).order_by('prioridade'))
    equipamento_status_resumo = list(equipamentos.values('status').annotate(total=Count('id')).order_by('status'))

    tempos_fechamento = [
        (chamado.data_fechamento - chamado.created_at).total_seconds() / 3600
        for chamado in chamados_encerrados.order_by('-data_fechamento', '-updated_at')[:50]
        if chamado.data_fechamento
    ]

    tempo_medio_fechamento = round(sum(tempos_fechamento) / len(tempos_fechamento), 1) if tempos_fechamento else 0

    return {
        'relatorios': {
            'chamados_total': chamados.count(),
            'chamados_abertos': chamados_abertos.count(),
            'chamados_encerrados': chamados_encerrados.count(),
            'chamados_criticos': chamados_abertos.filter(prioridade=PrioridadeChamado.CRITICA).count(),
            'chamados_aguardando_aprovacao': chamados.filter(fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_APROVACAO).count(),
            'equipamentos_total': equipamentos.count(),
            'equipamentos_em_estoque': equipamentos.filter(status=StatusEquipamento.EM_ESTOQUE).count(),
            'equipamentos_em_uso': equipamentos.filter(status=StatusEquipamento.EM_USO).count(),
            'equipamentos_em_manutencao': equipamentos.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
            'equipamentos_alerta': equipamentos.filter(score_saude__lt=70).count(),
            'reservas_ativas': reservas_ativas_queryset().count(),
            'usuarios_pendentes': Usuario.objects.filter(solicitacao_pendente=True).count(),
            'usuarios_ativos': Usuario.objects.filter(ativo=True, solicitacao_pendente=False).count(),
            'tempo_medio_fechamento': tempo_medio_fechamento,
        },
        'dashboard_charts': {
            'chamados_por_status': build_choice_chart(chamado_status_resumo, StatusChamado.choices, 'status'),
            'chamados_por_prioridade': build_choice_chart(
                chamado_prioridade_resumo,
                PrioridadeChamado.choices,
                'prioridade',
            ),
            'equipamentos_por_status': build_choice_chart(
                equipamento_status_resumo,
                StatusEquipamento.choices,
                'status',
            ),
            'chamados_abertos_por_prioridade': build_choice_chart(
                chamado_prioridade_resumo,
                PrioridadeChamado.choices,
                'prioridade',
            ),
        },
        'atividade_recente': LogEntry.objects.filter(
            content_type__app_label__in=['accounts', 'chamados', 'equipamentos', 'estoque']
        )
        .select_related('actor', 'content_type')
        .order_by('-timestamp')[:20],
        'chamados_recentes': chamados.order_by('-updated_at', '-created_at')[:10],
    }


@login_required
def relatorios_view(request):
    if not request.user.is_operacional:
        return redirect('dashboard')

    context = build_relatorios_context()
    return render(request, 'dashboard/relatorios.html', context)


@login_required
def auditoria_api_view(request):
    if not request.user.is_operacional:
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)

    entries = (
        LogEntry.objects.filter(content_type__app_label__in=['accounts', 'chamados', 'equipamentos', 'estoque'])
        .select_related('actor', 'content_type')
        .order_by('-timestamp')[:50]
    )
    return JsonResponse(
        {
            'results': [
                {
                    'object_repr': entry.object_repr,
                    'actor': entry.actor.nome_completo if entry.actor else None,
                    'action': entry.get_action_display(),
                    'content_type': entry.content_type.model,
                    'timestamp': timezone.localtime(entry.timestamp).strftime('%d/%m/%Y %H:%M'),
                }
                for entry in entries
            ]
        }
    )


@login_required
def busca_view(request):
    payload = build_search_payload(request.user, request.GET.get('q', ''))
    return render(request, 'dashboard/busca.html', payload)


@login_required
def busca_global_api(request):
    return JsonResponse(build_search_payload(request.user, request.GET.get('q', '')))
