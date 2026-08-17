import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connections, transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from equipamentos.health import build_telemetry_health
from equipamentos.models import AgenteMonitoramento, DivergenciaInventario, Equipamento, StatusMonitoramento
from notifications.services import notificar_admins

from .backup_service import get_backup_task_status, list_backup_sets
from .models import (
    RestoreTestResult,
    RestoreValidation,
    SystemHealthComponent,
    SystemHealthEvent,
    SystemHealthStatus,
)


@dataclass(frozen=True)
class HealthDiagnostic:
    key: str
    name: str
    status: str
    summary: str
    details: dict = field(default_factory=dict)
    notify: bool = True


def _database_diagnostic():
    try:
        connection = connections['default']
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:
        return HealthDiagnostic(
            'database',
            'Banco de dados',
            SystemHealthStatus.CRITICAL,
            'Banco de dados indisponivel.',
            {'engine': settings.DATABASES['default']['ENGINE'], 'error': str(exc)},
        )
    return HealthDiagnostic(
        'database',
        'Banco de dados',
        SystemHealthStatus.HEALTHY,
        'Conexao e consulta respondendo.',
        {'engine': settings.DATABASES['default']['ENGINE']},
    )


def _redis_diagnostic():
    key = f'itam:health:{uuid.uuid4()}'
    try:
        cache.set(key, 'ok', timeout=30)
        value = cache.get(key)
        cache.delete(key)
        if value != 'ok':
            raise RuntimeError('O valor de teste nao retornou do cache.')
    except Exception as exc:
        return HealthDiagnostic(
            'redis',
            'Redis e cache',
            SystemHealthStatus.CRITICAL,
            'Cache indisponivel ou sem resposta valida.',
            {'error': str(exc)},
        )
    return HealthDiagnostic(
        'redis',
        'Redis e cache',
        SystemHealthStatus.HEALTHY,
        'Leitura e gravacao respondendo.',
        {'backend': settings.CACHES['default']['BACKEND']},
    )


def _celery_diagnostic(source):
    now = timezone.now()
    if source == 'scheduled':
        return HealthDiagnostic(
            'celery',
            'Automacoes',
            SystemHealthStatus.HEALTHY,
            'Worker processou a verificacao automatica.',
            {'heartbeat_at': now.isoformat()},
        )

    current = SystemHealthComponent.objects.filter(component_key='celery').first()
    heartbeat_at = None
    if current:
        raw_heartbeat = (current.details or {}).get('heartbeat_at')
        try:
            heartbeat_at = datetime.fromisoformat(raw_heartbeat) if raw_heartbeat else None
        except (TypeError, ValueError):
            heartbeat_at = None
        if heartbeat_at and timezone.is_naive(heartbeat_at):
            heartbeat_at = timezone.make_aware(heartbeat_at, timezone.get_current_timezone())
    if heartbeat_at is None or heartbeat_at < now - timedelta(minutes=15):
        return HealthDiagnostic(
            'celery',
            'Automacoes',
            SystemHealthStatus.WARNING,
            'Worker sem confirmacao recente.',
            {'heartbeat_at': heartbeat_at.isoformat() if heartbeat_at else ''},
            notify=bool(heartbeat_at),
        )
    return HealthDiagnostic(
        'celery',
        'Automacoes',
        SystemHealthStatus.HEALTHY,
        'Worker confirmou atividade recentemente.',
        {'heartbeat_at': heartbeat_at.isoformat()},
    )


def _telemetry_diagnostic():
    now = timezone.now()
    stale_minutes = getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10)
    cutoff = now - timedelta(minutes=stale_minutes)
    monitored = Equipamento.objects.filter(monitoramento_ativo=True)
    monitored_count = monitored.count()
    active_agents = AgenteMonitoramento.objects.filter(ativo=True)
    active_agent_count = active_agents.count()
    stale = monitored.filter(Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=cutoff))
    stale_count = stale.count()
    online_count = monitored.filter(
        monitoramento_status=StatusMonitoramento.ONLINE,
        last_seen_at__gte=cutoff,
    ).count()
    alert_count = monitored.filter(
        monitoramento_status=StatusMonitoramento.ALERTA,
        last_seen_at__gte=cutoff,
    ).count()
    active_divergences = DivergenciaInventario.objects.filter(ativa=True).select_related('equipamento')
    divergence_count = active_divergences.count()
    divergence_counts = {
        item['equipamento_id']: item['total']
        for item in active_divergences.values('equipamento_id').annotate(total=Count('id'))
    }
    divergence_items = [
        {
            'asset_id': divergence.equipamento.id_patrimonio,
            'field': divergence.campo,
            'field_label': divergence.get_campo_display(),
            'registered_value': divergence.valor_cadastrado,
            'detected_value': divergence.valor_detectado,
            'checked_at': divergence.ultima_verificacao_em.isoformat(),
        }
        for divergence in active_divergences.order_by('-ultima_verificacao_em')[:10]
    ]
    latest_asset = (
        monitored.exclude(last_seen_at__isnull=True)
        .select_related('last_telemetria_agente')
        .order_by('-last_seen_at')
        .first()
    )
    last_heartbeat_at = latest_asset.last_seen_at if latest_asset else None
    stale_assets = list(stale.order_by('last_seen_at').values_list('id_patrimonio', flat=True)[:10])
    asset_health_issues = []
    health_warning_count = 0
    health_critical_count = 0
    for asset in monitored.select_related('last_telemetria_agente'):
        health = build_telemetry_health(asset, divergence_count=divergence_counts.get(asset.pk, 0), now=now)
        if health['status'] == SystemHealthStatus.WARNING:
            health_warning_count += 1
        elif health['status'] == SystemHealthStatus.CRITICAL:
            health_critical_count += 1
        if health['status'] in {SystemHealthStatus.WARNING, SystemHealthStatus.CRITICAL} and len(asset_health_issues) < 10:
            asset_health_issues.append(
                {
                    'asset_id': asset.id_patrimonio,
                    'status': health['status'],
                    'status_label': health['status_label'],
                    'summary': health['summary'],
                }
            )
    details = {
        'monitored_count': monitored_count,
        'online_count': online_count,
        'alert_count': alert_count,
        'stale_count': stale_count,
        'active_agent_count': active_agent_count,
        'divergence_count': divergence_count,
        'divergences': divergence_items,
        'health_warning_count': health_warning_count,
        'health_critical_count': health_critical_count,
        'health_assets': asset_health_issues,
        'last_heartbeat_at': last_heartbeat_at.isoformat() if last_heartbeat_at else '',
        'last_heartbeat_asset': latest_asset.id_patrimonio if latest_asset else '',
        'last_heartbeat_agent': (
            latest_asset.last_telemetria_agente.nome
            if latest_asset and latest_asset.last_telemetria_agente
            else ''
        ),
        'stale_minutes': stale_minutes,
        'stale_assets': stale_assets,
    }

    if monitored_count == 0:
        return HealthDiagnostic(
            'telemetry',
            'Agentes e telemetria',
            SystemHealthStatus.WARNING,
            'Nenhum equipamento possui monitoramento ativo.',
            details,
            notify=False,
        )
    if active_agent_count == 0:
        return HealthDiagnostic(
            'telemetry',
            'Agentes e telemetria',
            SystemHealthStatus.CRITICAL,
            'Ha equipamentos monitorados, mas nenhum agente ativo.',
            details,
        )
    if stale_count:
        return HealthDiagnostic(
            'telemetry',
            'Agentes e telemetria',
            SystemHealthStatus.CRITICAL,
            f'{stale_count} equipamento(s) sem heartbeat recente.',
            details,
        )
    if health_critical_count:
        return HealthDiagnostic(
            'telemetry',
            'Agentes e telemetria',
            SystemHealthStatus.CRITICAL,
            f'{health_critical_count} equipamento(s) com saúde crítica na última leitura.',
            details,
        )
    if health_warning_count:
        return HealthDiagnostic(
            'telemetry',
            'Agentes e telemetria',
            SystemHealthStatus.WARNING,
            f'{health_warning_count} equipamento(s) requer atenção na última leitura.',
            details,
        )
    if divergence_count:
        return HealthDiagnostic(
            'telemetry',
            'Agentes e telemetria',
            SystemHealthStatus.WARNING,
            f'{divergence_count} divergencia(s) entre o agente e o cadastro.',
            details,
        )
    if alert_count:
        return HealthDiagnostic(
            'telemetry',
            'Agentes e telemetria',
            SystemHealthStatus.WARNING,
            f'{alert_count} equipamento(s) enviando alerta de telemetria.',
            details,
        )
    return HealthDiagnostic(
        'telemetry',
        'Agentes e telemetria',
        SystemHealthStatus.HEALTHY,
        f'{online_count} equipamento(s) respondendo dentro da janela esperada.',
        details,
    )


def _disk_diagnostic():
    usage = shutil.disk_usage(settings.BASE_DIR)
    free_percent = round((usage.free / usage.total) * 100, 1) if usage.total else 0
    if free_percent < 5:
        status = SystemHealthStatus.CRITICAL
        summary = 'Espaco livre em nivel critico.'
    elif free_percent < 15:
        status = SystemHealthStatus.WARNING
        summary = 'Espaco livre abaixo do recomendado.'
    else:
        status = SystemHealthStatus.HEALTHY
        summary = 'Espaco suficiente para a operacao.'
    return HealthDiagnostic(
        'disk',
        'Armazenamento',
        status,
        summary,
        {
            'free_percent': free_percent,
            'free_bytes': usage.free,
            'used_bytes': usage.used,
            'total_bytes': usage.total,
        },
    )


def _backup_diagnostic():
    now = timezone.now()
    backups = [item for item in list_backup_sets(limit=10) if item.status == 'complete']
    task = get_backup_task_status()
    latest = backups[0] if backups else None
    details = {
        'latest_at': latest.created_at.isoformat() if latest else '',
        'latest_manifest': latest.manifest_file if latest else '',
        'restorable_count': sum(item.restorable for item in backups),
        'task_state': task.state,
        'task_result': task.last_result,
        'next_run_at': task.next_run.isoformat() if task.next_run else '',
    }
    if task.error or not task.installed:
        return HealthDiagnostic(
            'backup',
            'Backups',
            SystemHealthStatus.CRITICAL,
            task.error or 'Tarefa automatica nao instalada.',
            details,
        )
    if task.last_result not in (None, 0, 267009, 0x41301):
        return HealthDiagnostic(
            'backup',
            'Backups',
            SystemHealthStatus.CRITICAL,
            f'Ultima execucao terminou com falha ({task.last_result}).',
            details,
        )
    if latest is None:
        return HealthDiagnostic(
            'backup',
            'Backups',
            SystemHealthStatus.CRITICAL,
            'Nenhum backup completo foi encontrado.',
            details,
        )

    age = now - latest.created_at
    details['age_hours'] = round(age.total_seconds() / 3600, 1)
    if age > timedelta(hours=48):
        return HealthDiagnostic(
            'backup',
            'Backups',
            SystemHealthStatus.CRITICAL,
            'Ultimo backup completo tem mais de 48 horas.',
            details,
        )
    if age > timedelta(hours=26):
        return HealthDiagnostic(
            'backup',
            'Backups',
            SystemHealthStatus.WARNING,
            'Ultimo backup completo tem mais de 26 horas.',
            details,
        )
    return HealthDiagnostic(
        'backup',
        'Backups',
        SystemHealthStatus.HEALTHY,
        'Backup recente e tarefa automatica disponivel.',
        details,
    )


def _restore_validation_diagnostic():
    latest = RestoreValidation.objects.select_related('recorded_by').first()
    if latest is None:
        return HealthDiagnostic(
            'restore_validation',
            'Teste de restauracao',
            SystemHealthStatus.WARNING,
            'Nenhum teste de restauracao foi registrado.',
            {},
            notify=False,
        )
    details = {
        'tested_at': latest.tested_at.isoformat(),
        'result': latest.result,
        'backup_manifest': latest.backup_manifest,
        'recorded_by': latest.recorded_by.nome_completo if latest.recorded_by else '',
    }
    if latest.result == RestoreTestResult.FAILED:
        return HealthDiagnostic(
            'restore_validation',
            'Teste de restauracao',
            SystemHealthStatus.CRITICAL,
            'O teste de restauracao mais recente falhou.',
            details,
        )
    if latest.tested_at < timezone.now() - timedelta(days=30):
        return HealthDiagnostic(
            'restore_validation',
            'Teste de restauracao',
            SystemHealthStatus.WARNING,
            'O ultimo teste aprovado tem mais de 30 dias.',
            details,
        )
    return HealthDiagnostic(
        'restore_validation',
        'Teste de restauracao',
        SystemHealthStatus.HEALTHY,
        'Recuperacao validada nos ultimos 30 dias.',
        details,
    )


def _security_diagnostic():
    is_production = settings.DJANGO_ENV == 'production'
    issues = []
    if is_production:
        if settings.DEBUG:
            issues.append('DEBUG ativo')
        if not settings.SECURE_SSL_REDIRECT:
            issues.append('HTTPS nao obrigatorio')
        if not settings.ALLOWED_HOSTS or settings.ALLOWED_HOSTS == ['*']:
            issues.append('hosts sem restricao')
    elif settings.DEBUG:
        return HealthDiagnostic(
            'security',
            'Ambiente e seguranca',
            SystemHealthStatus.WARNING,
            'Ambiente de homologacao com DEBUG ativo.',
            {'environment': settings.DJANGO_ENV},
            notify=False,
        )
    if issues:
        return HealthDiagnostic(
            'security',
            'Ambiente e seguranca',
            SystemHealthStatus.CRITICAL,
            'Configuracao de producao requer correcao.',
            {'environment': settings.DJANGO_ENV, 'issues': issues},
        )
    return HealthDiagnostic(
        'security',
        'Ambiente e seguranca',
        SystemHealthStatus.HEALTHY,
        'Configuracao coerente com o ambiente.',
        {'environment': settings.DJANGO_ENV},
    )


def collect_health_diagnostics(source='manual'):
    return [
        _database_diagnostic(),
        _redis_diagnostic(),
        _celery_diagnostic(source),
        _telemetry_diagnostic(),
        _disk_diagnostic(),
        _backup_diagnostic(),
        _restore_validation_diagnostic(),
        _security_diagnostic(),
    ]


def _notification_for(component, previous_status, notify):
    problem_statuses = {SystemHealthStatus.WARNING, SystemHealthStatus.CRITICAL}
    if not notify:
        return None
    if component.status in problem_statuses:
        if component.last_notified_status == component.status:
            return None
        title = f'Alerta de saude: {component.name}'
        message = component.summary
        notified_status = component.status
    elif component.status == SystemHealthStatus.HEALTHY and component.last_notified_status in problem_statuses:
        title = f'Servico recuperado: {component.name}'
        message = component.summary
        notified_status = SystemHealthStatus.HEALTHY
    else:
        return None
    return title, message, notified_status


def persist_health_diagnostics(diagnostics, source='manual'):
    now = timezone.now()
    notifications = []
    with transaction.atomic():
        for diagnostic in diagnostics:
            component = SystemHealthComponent.objects.select_for_update().filter(
                component_key=diagnostic.key
            ).first()
            previous_status = component.status if component else ''
            status_changed = component is None or previous_status != diagnostic.status
            if component is None:
                component = SystemHealthComponent(
                    component_key=diagnostic.key,
                    status_changed_at=now,
                    checked_at=now,
                )
            component.name = diagnostic.name
            component.status = diagnostic.status
            component.summary = diagnostic.summary
            component.details = diagnostic.details
            component.source = source
            component.checked_at = now
            if status_changed:
                component.status_changed_at = now
            notification = _notification_for(component, previous_status, diagnostic.notify) if status_changed else None
            if notification:
                component.last_notified_status = notification[2]
                notifications.append(notification[:2])
            component.save()

            if status_changed:
                SystemHealthEvent.objects.create(
                    component_key=diagnostic.key,
                    component_name=diagnostic.name,
                    previous_status=previous_status,
                    status=diagnostic.status,
                    summary=diagnostic.summary,
                    details=diagnostic.details,
                )

    for title, message in notifications:
        notificar_admins(title, message, reverse('system_health'))
    return list(SystemHealthComponent.objects.all())


def perform_system_health_checks(source='manual'):
    diagnostics = collect_health_diagnostics(source=source)
    return persist_health_diagnostics(diagnostics, source=source)


def overall_health_status(components):
    statuses = {component.status for component in components}
    if SystemHealthStatus.CRITICAL in statuses:
        return SystemHealthStatus.CRITICAL
    if SystemHealthStatus.WARNING in statuses or SystemHealthStatus.UNKNOWN in statuses:
        return SystemHealthStatus.WARNING
    return SystemHealthStatus.HEALTHY
