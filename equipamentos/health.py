"""Diagnostico operacional baseado na ultima telemetria do equipamento."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import StatusMonitoramento

HEALTHY = 'healthy'
WARNING = 'warning'
CRITICAL = 'critical'
UNKNOWN = 'unknown'

STATUS_LABELS = {
    HEALTHY: 'Saudável',
    WARNING: 'Atenção',
    CRITICAL: 'Crítico',
    UNKNOWN: 'Sem dados',
}

STATUS_RANK = {
    HEALTHY: 0,
    UNKNOWN: 1,
    WARNING: 2,
    CRITICAL: 3,
}


def _number(value):
    if value in {None, ''}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentage(value):
    if value is None:
        return None
    return max(0.0, min(100.0, round(value, 1)))


def _status_for_lower_value(value, warning_at, critical_at):
    if value is None:
        return UNKNOWN
    if value <= critical_at:
        return CRITICAL
    if value <= warning_at:
        return WARNING
    return HEALTHY


def _status_for_higher_value(value, warning_at, critical_at):
    if value is None:
        return UNKNOWN
    if value >= critical_at:
        return CRITICAL
    if value >= warning_at:
        return WARNING
    return HEALTHY


def _metric(key, label, icon, value, caption, percent, status, issue=''):
    return {
        'key': key,
        'label': label,
        'icon': icon,
        'value': value,
        'caption': caption,
        'percent': _percentage(percent),
        'status': status,
        'status_label': STATUS_LABELS[status],
        'issue': issue if status in {WARNING, CRITICAL} else '',
    }


def _cpu_metric(payload):
    value = _number(payload.get('cpu_load_percent'))
    status = _status_for_higher_value(value, warning_at=85, critical_at=95)
    display = f'{value:.0f}%' if value is not None else '-'
    issue = f'CPU em {value:.0f}% na última leitura.' if value is not None else ''
    return _metric('cpu', 'CPU', 'fa-microchip', display, 'Uso instantâneo', value, status, issue)


def _memory_metric(payload):
    total_mb = _number(payload.get('memory_total_mb'))
    free_mb = _number(payload.get('memory_free_mb'))
    free_percent = (free_mb / total_mb) * 100 if total_mb and free_mb is not None else None
    status = _status_for_lower_value(free_percent, warning_at=15, critical_at=5)
    display = f'{free_percent:.0f}% livre' if free_percent is not None else '-'
    caption = 'Memória disponível não informada'
    if total_mb and free_mb is not None:
        caption = f'{free_mb / 1024:.1f} GB livres de {total_mb / 1024:.1f} GB'
    issue = f'Memória com apenas {free_percent:.0f}% livre.' if free_percent is not None else ''
    return _metric('memory', 'Memória', 'fa-memory', display, caption, free_percent, status, issue)


def _disk_metric(payload):
    free_percent = _number(payload.get('disk_free_percent'))
    disks = payload.get('disks') if isinstance(payload.get('disks'), list) else []
    valid_disks = [disk for disk in disks if isinstance(disk, dict)]
    if free_percent is None:
        percentages = [_number(disk.get('free_percent')) for disk in valid_disks]
        percentages = [value for value in percentages if value is not None]
        free_percent = min(percentages) if percentages else None

    status = _status_for_lower_value(free_percent, warning_at=10, critical_at=5)
    display = f'{free_percent:.0f}% livre' if free_percent is not None else '-'
    caption = 'Espaço disponível não informado'
    if valid_disks:
        lowest = min(
            valid_disks,
            key=lambda disk: _number(disk.get('free_percent'))
            if _number(disk.get('free_percent')) is not None
            else 101,
        )
        free_gb = _number(lowest.get('free_gb'))
        size_gb = _number(lowest.get('size_gb'))
        drive_name = str(lowest.get('name') or 'Disco')
        if free_gb is not None and size_gb is not None:
            caption = f'{drive_name} {free_gb:.1f} GB livres de {size_gb:.1f} GB'
    issue = f'Disco com apenas {free_percent:.0f}% livre.' if free_percent is not None else ''
    return _metric('disk', 'Disco', 'fa-hard-drive', display, caption, free_percent, status, issue)


def _battery_metric(payload):
    level = _number(payload.get('battery_level'))
    status = _status_for_lower_value(level, warning_at=25, critical_at=15)
    display = f'{level:.0f}%' if level is not None else '-'
    caption = 'Carga atual informada pelo Windows' if level is not None else 'Bateria não detectada'
    issue = f'Bateria com apenas {level:.0f}% de carga.' if level is not None else ''
    return _metric('battery', 'Bateria', 'fa-battery-half', display, caption, level, status, issue)


def _connection_status(equipment, now):
    stale_minutes = getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10)
    if not equipment.monitoramento_ativo:
        return UNKNOWN, 'Não monitorado', stale_minutes
    if equipment.last_seen_at is None:
        return CRITICAL, 'Nunca recebido', stale_minutes
    if equipment.last_seen_at < now - timedelta(minutes=stale_minutes):
        return CRITICAL, 'Sem sinal', stale_minutes
    if equipment.monitoramento_status == StatusMonitoramento.OFFLINE:
        return CRITICAL, 'Sem sinal', stale_minutes
    if equipment.monitoramento_status == StatusMonitoramento.ALERTA:
        return WARNING, 'Online com alerta', stale_minutes
    if equipment.monitoramento_status == StatusMonitoramento.ONLINE:
        return HEALTHY, 'Online', stale_minutes
    return UNKNOWN, 'Sem status', stale_minutes


def _parse_payload_datetime(value):
    if not value:
        return None
    try:
        parsed = parse_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def build_telemetry_health(equipment, divergence_count=0, now=None):
    now = now or timezone.now()
    payload = equipment.last_telemetria_payload if isinstance(equipment.last_telemetria_payload, dict) else {}
    metrics = [
        _cpu_metric(payload),
        _memory_metric(payload),
        _disk_metric(payload),
        _battery_metric(payload),
    ]
    connection_status, connection_label, stale_minutes = _connection_status(equipment, now)
    relevant_statuses = [connection_status]
    relevant_statuses.extend(metric['status'] for metric in metrics if metric['status'] != UNKNOWN)

    reported_severity = str(payload.get('severity') or '').lower()
    if reported_severity == CRITICAL:
        relevant_statuses.append(CRITICAL)
    elif reported_severity == WARNING:
        relevant_statuses.append(WARNING)
    if divergence_count:
        relevant_statuses.append(WARNING)

    has_reported_health = payload and any(metric['status'] != UNKNOWN for metric in metrics)
    if has_reported_health or divergence_count or reported_severity in {WARNING, CRITICAL}:
        status = max(relevant_statuses, key=STATUS_RANK.get)
    elif connection_status == HEALTHY:
        status = UNKNOWN
    else:
        status = connection_status

    issues = [metric['issue'] for metric in metrics if metric['issue']]
    message = str(payload.get('message') or '').strip()
    if reported_severity in {WARNING, CRITICAL} and message and message not in issues:
        issues.append(message)
    if divergence_count:
        suffix = 'divergência de inventário ativa' if divergence_count == 1 else 'divergências de inventário ativas'
        issues.append(f'{divergence_count} {suffix}.')

    if not equipment.monitoramento_ativo:
        summary = 'Monitoramento não está ativo para este equipamento.'
    elif connection_status == CRITICAL:
        summary = f'Heartbeat ausente há mais de {stale_minutes} minutos.'
    elif status in {WARNING, CRITICAL} and issues:
        summary = ' '.join(issues[:2])
    elif status == HEALTHY:
        summary = 'Heartbeat recente e indicadores dentro dos limites.'
    else:
        summary = 'Agente conectado, mas sem indicadores suficientes para avaliar a saúde.'

    agent = equipment.last_telemetria_agente
    metadata = agent.metadata if agent and isinstance(agent.metadata, dict) else {}
    return {
        'status': status,
        'status_label': STATUS_LABELS[status],
        'summary': summary,
        'connection_status': connection_status,
        'connection_label': connection_label,
        'stale_minutes': stale_minutes,
        'metrics': metrics,
        'last_seen_at': equipment.last_seen_at,
        'agent_name': agent.nome if agent else '',
        'host_name': agent.host_name if agent else '',
        'os_caption': str(payload.get('os_caption') or ''),
        'os_version': str(payload.get('os_version') or ''),
        'logged_user': str(payload.get('logged_user') or ''),
        'last_boot_at': _parse_payload_datetime(payload.get('last_boot_at')),
        'manufacturer': str(metadata.get('manufacturer') or ''),
        'machine_model': str(metadata.get('model') or ''),
        'divergence_count': divergence_count,
    }
