import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connections, transaction
from django.urls import reverse
from django.utils import timezone

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
