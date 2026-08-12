from collections import defaultdict
from datetime import timedelta

from auditlog.models import LogEntry
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Usuario
from chamados.models import Chamado, EtapaFluxoChamado, PrioridadeChamado, SLANivel, StatusChamado
from chamados.views import painel_tecnico as painel_tecnico_view
from equipamentos.models import Equipamento, StatusEquipamento
from estoque.models import reservas_ativas_queryset
from estoque.views import estoque_view as estoque_workspace_view
from itam.charting import build_choice_chart
from notifications.models import Notification

from .backup_service import (
    BackupOperationError,
    configure_backup_task,
    get_backup_task_status,
    get_restore_status,
    list_backup_sets,
    run_backup_now,
    start_restore_point,
)
from .forms import BackupConfigurationForm, RestoreValidationForm
from .health_service import overall_health_status, perform_system_health_checks
from .models import (
    BackupConfiguration,
    RestoreValidation,
    SystemHealthComponent,
    SystemHealthEvent,
    SystemHealthStatus,
)
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


def _backup_chart(backup_sets):
    daily = defaultdict(lambda: {'count': 0, 'bytes': 0})
    for backup_set in backup_sets:
        local_date = timezone.localtime(backup_set.created_at).date()
        daily[local_date]['count'] += 1
        daily[local_date]['bytes'] += backup_set.total_bytes

    dates = sorted(daily)
    return {
        'labels': [item.strftime('%d/%m') for item in dates],
        'counts': [daily[item]['count'] for item in dates],
        'sizes_mb': [round(daily[item]['bytes'] / (1024 * 1024), 2) for item in dates],
    }


@login_required
def backup_configuration_view(request):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    configuration = BackupConfiguration.load()
    form = BackupConfigurationForm(request.POST or None, instance=configuration)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'run_now':
            requested_at = timezone.now()
            try:
                run_backup_now()
            except BackupOperationError as exc:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'started': False, 'error': str(exc)}, status=500)
                messages.error(request, f'Nao foi possivel iniciar o backup: {exc}')
            else:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'started': True, 'requested_at': requested_at.isoformat()})
                messages.success(request, 'Backup iniciado. O historico sera atualizado quando ele terminar.')
            return redirect('backup_configuration')

        if action == 'restore':
            if request.POST.get('confirmation') != 'RESTAURAR':
                return JsonResponse({'started': False, 'error': 'Confirmacao invalida.'}, status=400)
            try:
                operation_id = start_restore_point(
                    request.POST.get('manifest', ''),
                    retention_days=configuration.retention_days,
                    schedule_times=configuration.schedule_times,
                )
            except BackupOperationError as exc:
                return JsonResponse({'started': False, 'error': str(exc)}, status=400)
            return JsonResponse(
                {
                    'started': True,
                    'operation_id': str(operation_id),
                    'status_url': reverse('restore_status', kwargs={'operation_id': operation_id}),
                }
            )

        if form.is_valid():
            try:
                configure_backup_task(
                    form.cleaned_data['retention_days'],
                    form.cleaned_data['schedule_times'],
                )
            except BackupOperationError as exc:
                form.add_error(None, f'Nao foi possivel atualizar o agendamento: {exc}')
            else:
                configuration = form.save(commit=False)
                configuration.updated_by = request.user
                configuration.save()
                messages.success(request, 'Configuracao de backup atualizada.')
                return redirect('backup_configuration')

    backup_sets = list_backup_sets(limit=None)
    total_size = sum(item.total_bytes for item in backup_sets)
    return render(
        request,
        'dashboard/backups.html',
        {
            'form': form,
            'configuration': configuration,
            'task_status': get_backup_task_status(),
            'backup_sets': backup_sets[:30],
            'backup_count': len(backup_sets),
            'restore_point_count': sum(item.restorable for item in backup_sets),
            'backup_total_size_mb': round(total_size / (1024 * 1024), 1),
            'backup_chart': _backup_chart(backup_sets),
        },
    )


@login_required
def backup_status_view(request):
    if not request.user.is_admin:
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)

    requested_at = parse_datetime(request.GET.get('requested_at', ''))
    if requested_at is None:
        return JsonResponse({'detail': 'Data da solicitacao invalida.'}, status=400)
    if timezone.is_naive(requested_at):
        requested_at = timezone.make_aware(requested_at, timezone.get_current_timezone())

    task_status = get_backup_task_status()
    completed_backups = [item for item in list_backup_sets(limit=5) if item.status == 'complete']
    latest_backup = completed_backups[0] if completed_backups else None
    complete = bool(latest_backup and latest_backup.created_at >= requested_at)
    running = task_status.state == 'Running' or task_status.last_result in (267009, 0x41301)
    failed = bool(
        not complete
        and not running
        and task_status.last_run
        and task_status.last_run >= requested_at
        and task_status.last_result not in (None, 0)
    )

    return JsonResponse(
        {
            'complete': complete,
            'running': running,
            'failed': failed,
            'task_state': task_status.state,
            'status_label': task_status.last_result_label,
            'latest_backup_at': latest_backup.created_at.isoformat() if latest_backup else None,
            'error': task_status.error,
        }
    )


def restore_status_view(request, operation_id):
    try:
        payload = get_restore_status(operation_id)
    except BackupOperationError as exc:
        return JsonResponse({'detail': str(exc)}, status=404)
    return JsonResponse(payload)


def _format_bytes(value):
    size = float(value or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} B'
        size /= 1024
    return f'{size:.1f} TB'


def _health_component_cards(components):
    ui = {
        'database': ('fa-database', 'Dados'),
        'redis': ('fa-bolt', 'Tempo real'),
        'celery': ('fa-gears', 'Automacoes'),
        'disk': ('fa-hard-drive', 'Servidor'),
        'backup': ('fa-box-archive', 'Continuidade'),
        'restore_validation': ('fa-clock-rotate-left', 'Recuperacao'),
        'security': ('fa-shield-halved', 'Ambiente'),
    }
    cards = []
    for component in components:
        icon, category = ui.get(component.component_key, ('fa-circle-info', 'Sistema'))
        detail_lines = []
        details = component.details or {}
        if component.component_key == 'disk':
            detail_lines = [
                f'{details.get("free_percent", 0)}% livre',
                f'{_format_bytes(details.get("free_bytes"))} disponiveis',
            ]
        elif component.component_key == 'backup':
            detail_lines = [
                f'{details.get("restorable_count", 0)} ponto(s) restauravel(is)',
                f'Tarefa: {details.get("task_state") or "indisponivel"}',
            ]
        elif component.component_key == 'database':
            engine = str(details.get('engine', '')).rsplit('.', 1)[-1]
            detail_lines = [engine.upper()] if engine else []
        elif component.component_key == 'security':
            detail_lines = [str(details.get('environment', '')).title()]
        elif component.component_key == 'restore_validation' and details.get('backup_manifest'):
            detail_lines = [details['backup_manifest']]
        elif component.component_key == 'redis':
            detail_lines = ['Cache e canais']
        elif component.component_key == 'celery':
            detail_lines = ['Worker e tarefas periodicas']

        cards.append(
            {
                'key': component.component_key,
                'name': component.name,
                'category': category,
                'icon': icon,
                'status': component.status,
                'status_label': component.get_status_display(),
                'summary': component.summary,
                'checked_at': component.checked_at,
                'status_changed_at': component.status_changed_at,
                'details': detail_lines,
                'disk_percent': details.get('free_percent') if component.component_key == 'disk' else None,
            }
        )
    return cards


def _health_chart(events):
    today = timezone.localdate()
    dates = [today - timedelta(days=offset) for offset in range(13, -1, -1)]
    daily = {date: {'issues': 0, 'recoveries': 0} for date in dates}
    for event in events:
        local_date = timezone.localtime(event.occurred_at).date()
        if local_date not in daily:
            continue
        if event.status == SystemHealthStatus.HEALTHY and event.previous_status in {
            SystemHealthStatus.WARNING,
            SystemHealthStatus.CRITICAL,
        }:
            daily[local_date]['recoveries'] += 1
        elif event.status in {SystemHealthStatus.WARNING, SystemHealthStatus.CRITICAL}:
            daily[local_date]['issues'] += 1
    return {
        'labels': [date.strftime('%d/%m') for date in dates],
        'issues': [daily[date]['issues'] for date in dates],
        'recoveries': [daily[date]['recoveries'] for date in dates],
    }


@login_required
def system_health_view(request):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    backup_sets = [backup for backup in list_backup_sets(limit=30) if backup.restorable]
    restore_form = RestoreValidationForm(
        backup_sets=backup_sets,
        initial={'tested_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M')},
    )
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'check_now':
            perform_system_health_checks(source='manual')
            messages.success(request, 'Diagnostico do sistema atualizado.')
            return redirect('system_health')
        if action == 'record_restore_test':
            restore_form = RestoreValidationForm(request.POST, backup_sets=backup_sets)
            if restore_form.is_valid():
                validation = restore_form.save(commit=False)
                validation.recorded_by = request.user
                validation.save()
                perform_system_health_checks(source='manual')
                messages.success(request, 'Teste de restauracao registrado.')
                return redirect('system_health')

    components = list(SystemHealthComponent.objects.all())
    newest_check = max((component.checked_at for component in components), default=None)
    if newest_check is None or newest_check < timezone.now() - timedelta(minutes=10):
        components = perform_system_health_checks(source='manual')
    events = list(SystemHealthEvent.objects.order_by('-occurred_at')[:50])
    overall_status = overall_health_status(components)
    status_meta = {
        SystemHealthStatus.HEALTHY: {
            'label': 'Operação saudável',
            'tone': 'success',
            'icon': 'fa-circle-check',
        },
        SystemHealthStatus.WARNING: {
            'label': 'Atenção necessária',
            'tone': 'warning',
            'icon': 'fa-triangle-exclamation',
        },
        SystemHealthStatus.CRITICAL: {
            'label': 'Ação imediata',
            'tone': 'danger',
            'icon': 'fa-circle-exclamation',
        },
    }[overall_status]
    last_checked_at = max((component.checked_at for component in components), default=None)
    context = {
        'component_cards': _health_component_cards(components),
        'overall_status': overall_status,
        'status_meta': status_meta,
        'healthy_count': sum(component.status == SystemHealthStatus.HEALTHY for component in components),
        'warning_count': sum(component.status == SystemHealthStatus.WARNING for component in components),
        'critical_count': sum(component.status == SystemHealthStatus.CRITICAL for component in components),
        'last_checked_at': last_checked_at,
        'events': events[:20],
        'health_chart': _health_chart(events),
        'restore_validations': RestoreValidation.objects.select_related('recorded_by')[:10],
        'restore_form': restore_form,
        'has_restore_points': bool(backup_sets),
    }
    return render(request, 'dashboard/system_health.html', context)


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
