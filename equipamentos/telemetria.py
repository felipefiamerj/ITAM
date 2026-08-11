"""Processamento de telemetria e heartbeat dos ativos."""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

from ia.monitoring import calcular_score
from notifications.services import notificar_time_operacional

from .models import AgenteMonitoramento, Equipamento, StatusMonitoramento, TelemetriaEvento

EVENTO_SEVERIDADE_PADRAO = {
    'heartbeat': 'info',
    'conectado': 'info',
    'desconectado': 'critical',
    'bateria_baixa': 'warning',
    'erro_driver': 'critical',
    'health_warning': 'warning',
}


def _limite_alerta_minutos():
    return getattr(settings, 'ITAM_MONITORING_ALERT_COOLDOWN_MINUTES', 30)


def _limite_stale_minutos():
    return getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10)


def _normalizar_evento(item):
    return str(item.get('event_type') or item.get('tipo') or 'heartbeat').strip().lower()


def _para_inteiro(value):
    if value in {None, ''}:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalizar_severidade(item, event_type, battery_level=None, signal_quality=None):
    severidade = str(item.get('severity') or item.get('severidade') or '').strip().lower()
    if severidade in {'info', 'warning', 'critical'}:
        return severidade

    if event_type == 'desconectado':
        return 'critical'
    if event_type in {'erro_driver', 'health_warning', 'bateria_baixa'}:
        return 'warning' if event_type != 'erro_driver' else 'critical'
    if battery_level is not None and battery_level <= 15:
        return 'warning'
    if signal_quality is not None and signal_quality <= 20:
        return 'warning'
    return EVENTO_SEVERIDADE_PADRAO.get(event_type, 'info')


def _estado_monitoramento(event_type, battery_level=None, signal_quality=None):
    if event_type == 'desconectado':
        return StatusMonitoramento.OFFLINE
    if event_type in {'erro_driver', 'health_warning', 'bateria_baixa'}:
        return StatusMonitoramento.ALERTA
    if battery_level is not None and battery_level <= 15:
        return StatusMonitoramento.ALERTA
    if signal_quality is not None and signal_quality <= 20:
        return StatusMonitoramento.ALERTA
    return StatusMonitoramento.ONLINE


def _mensagem_padrao(equipamento, agent_label, event_type):
    descricao = equipamento.tipo_display or equipamento.id_patrimonio
    if event_type == 'heartbeat':
        return f'{descricao} respondeu heartbeat em {agent_label}.'
    if event_type == 'conectado':
        return f'{descricao} conectado em {agent_label}.'
    if event_type == 'desconectado':
        return f'{descricao} desconectado de {agent_label}.'
    if event_type == 'bateria_baixa':
        return f'{descricao} com bateria baixa em {agent_label}.'
    if event_type == 'erro_driver':
        return f'{descricao} reportou erro de driver em {agent_label}.'
    return f'{descricao} gerou alerta em {agent_label}.'


def _resolver_equipamento(item):
    for campo in ('id_patrimonio', 'patrimonio', 'asset_id'):
        valor = (item.get(campo) or '').strip()
        if not valor:
            continue
        equipamento = Equipamento.objects.filter(id_patrimonio__iexact=valor).first()
        if equipamento:
            return equipamento

    for campo, lookup in (
        ('service_tag', 'service_tag__iexact'),
        ('numero_serie', 'numero_serie__iexact'),
        ('serial', 'numero_serie__iexact'),
        ('imei', 'imei__iexact'),
    ):
        valor = (item.get(campo) or '').strip()
        if not valor:
            continue
        equipamento = Equipamento.objects.filter(**{lookup: valor}).first()
        if equipamento:
            return equipamento
    return None


def _deve_notificar(equipamento, event_type, severidade):
    if severidade == 'info':
        return False

    ultima_alerta = (
        TelemetriaEvento.objects.filter(
            equipamento=equipamento,
            tipo=event_type,
            severidade=severidade,
        )
        .order_by('-created_at')
        .first()
    )
    if not ultima_alerta:
        return True
    return ultima_alerta.created_at < timezone.now() - timedelta(minutes=_limite_alerta_minutos())


def _notificar_evento(equipamento, agente, event_type, severidade, mensagem):
    if not _deve_notificar(equipamento, event_type, severidade):
        return

    agente_label = 'agente'
    if agente:
        agente_label = agente.host_name or agente.nome or agente_label
    titulo = f'Alerta de telemetria: {equipamento.id_patrimonio}'
    detalhe = f'{mensagem} Agente: {agente_label}.'
    link = reverse('detalhe_equipamento', args=[equipamento.id_patrimonio])
    notificar_time_operacional(titulo, detalhe, link)


@transaction.atomic
def processar_pacote_telemetria(payload, remote_ip=None):
    if not isinstance(payload, dict):
        raise ValidationError('Payload inválido.')

    token = (payload.get('agent_token') or payload.get('token') or '').strip()
    if not token:
        raise ValidationError('Informe agent_token.')

    agente = AgenteMonitoramento.objects.select_for_update().filter(token=token, ativo=True).first()
    if not agente:
        raise PermissionDenied('Agente de monitoramento inválido ou inativo.')

    agora = timezone.now()
    agente.host_name = (payload.get('host_name') or payload.get('machine_name') or agente.host_name or '').strip()
    agente.last_ip = (payload.get('ip') or remote_ip or agente.last_ip or '').strip() or None
    agente.last_seen_at = agora
    update_fields = ['host_name', 'last_ip', 'last_seen_at', 'updated_at']
    if isinstance(payload.get('metadata'), dict):
        agente.metadata = payload['metadata']
        update_fields.append('metadata')
    agente.save(update_fields=update_fields)

    devices = payload.get('devices')
    if devices is None:
        devices = [payload]
    if not isinstance(devices, list):
        raise ValidationError('devices precisa ser uma lista.')

    processados = 0
    erros = []
    alertas = 0

    for item in devices:
        if not isinstance(item, dict):
            erros.append('Item de telemetria inválido.')
            continue

        equipamento = _resolver_equipamento(item)
        if equipamento is None:
            identificador = item.get('id_patrimonio') or item.get('service_tag') or item.get('numero_serie') or item.get('serial') or item.get('imei')
            erros.append(f'Equipamento não encontrado para {identificador or "registro sem identificador"}.')
            continue

        event_type = _normalizar_evento(item)
        battery_level = _para_inteiro(item.get('battery_level', item.get('battery')))
        signal_quality = _para_inteiro(item.get('signal_quality', item.get('signal')))
        severidade = _normalizar_severidade(item, event_type, battery_level=battery_level, signal_quality=signal_quality)
        estado = _estado_monitoramento(event_type, battery_level=battery_level, signal_quality=signal_quality)
        mensagem = (item.get('message') or item.get('mensagem') or '').strip()
        if not mensagem:
            mensagem = _mensagem_padrao(equipamento, agente.host_name or agente.nome, event_type)

        equipamento.monitoramento_ativo = True
        equipamento.monitoramento_status = estado
        equipamento.last_seen_at = agora
        equipamento.last_telemetria_agente = agente
        equipamento.last_telemetria_payload = item
        equipamento.score_saude = calcular_score(equipamento)
        equipamento.save(
            update_fields=[
                'monitoramento_ativo',
                'monitoramento_status',
                'last_seen_at',
                'last_telemetria_agente',
                'last_telemetria_payload',
                'score_saude',
                'updated_at',
            ]
        )

        if severidade in {'warning', 'critical'}:
            alertas += 1
            _notificar_evento(equipamento, agente, event_type, severidade, mensagem)

        TelemetriaEvento.objects.create(
            equipamento=equipamento,
            agente=agente,
            tipo=event_type,
            severidade=severidade,
            mensagem=mensagem,
            payload=item,
        )

        processados += 1

    return {
        'agente': agente,
        'processados': processados,
        'erros': erros,
        'alertas': alertas,
        'stale_minutes': _limite_stale_minutos(),
    }


def equipamentos_sem_sinal_queryset():
    limite = timezone.now() - timedelta(minutes=_limite_stale_minutos())
    return Equipamento.objects.filter(monitoramento_ativo=True).filter(
        models.Q(last_seen_at__isnull=True) | models.Q(last_seen_at__lt=limite)
    )


@transaction.atomic
def marcar_equipamentos_sem_sinal():
    limite = timezone.now() - timedelta(minutes=_limite_stale_minutos())
    equipamentos = (
        Equipamento.objects.select_for_update()
        .prefetch_related('last_telemetria_agente')
        .filter(monitoramento_ativo=True)
        .filter(models.Q(last_seen_at__isnull=True) | models.Q(last_seen_at__lt=limite))
        .exclude(monitoramento_status=StatusMonitoramento.OFFLINE)
    )

    atualizados = 0
    for equipamento in equipamentos:
        agente = equipamento.last_telemetria_agente
        mensagem = (
            f'{equipamento.tipo_display} sem heartbeat há mais de {_limite_stale_minutos()} minutos.'
        )

        equipamento.monitoramento_status = StatusMonitoramento.OFFLINE
        equipamento.score_saude = calcular_score(equipamento)
        equipamento.save(update_fields=['monitoramento_status', 'score_saude', 'updated_at'])

        _notificar_evento(equipamento, agente, 'desconectado', 'critical', mensagem)
        TelemetriaEvento.objects.create(
            equipamento=equipamento,
            agente=agente,
            tipo='desconectado',
            severidade='critical',
            mensagem=mensagem,
            payload={'source': 'stale-check', 'stale_minutes': _limite_stale_minutos()},
        )
        atualizados += 1

    return atualizados
