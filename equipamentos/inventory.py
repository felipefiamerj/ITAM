import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from notifications.services import notificar_time_operacional

from .models import CampoDivergenciaInventario, DivergenciaInventario


def _number(value):
    if value in {None, ''}:
        return None
    try:
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalized_identifier(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def _normalized_windows_user(value):
    user = str(value or '').strip().casefold().replace('/', '\\').rsplit('\\', 1)[-1]
    return user.split('@', 1)[0]


def _storage_total(payload):
    disks = payload.get('disks')
    if not isinstance(disks, list):
        return None
    sizes = [_number(item.get('size_gb')) for item in disks if isinstance(item, dict)]
    sizes = [size for size in sizes if size is not None]
    return sum(sizes, Decimal('0')) if sizes else None


def _comparisons(equipment, payload):
    specifications = equipment.especificacoes or {}
    rows = {}

    expected_memory = _number(specifications.get('memoria_gb'))
    detected_memory_mb = _number(payload.get('memory_total_mb'))
    if expected_memory is not None and detected_memory_mb is not None:
        detected_memory = detected_memory_mb / Decimal('1024')
        tolerance = max(Decimal('1'), expected_memory * Decimal('0.10'))
        rows[CampoDivergenciaInventario.MEMORIA] = (
            f'{expected_memory.normalize()} GB',
            f'{detected_memory.quantize(Decimal("0.1"))} GB',
            abs(expected_memory - detected_memory) <= tolerance,
        )

    expected_storage = _number(specifications.get('armazenamento_gb'))
    detected_storage = _storage_total(payload)
    if expected_storage is not None and detected_storage is not None:
        tolerance = max(Decimal('10'), expected_storage * Decimal('0.12'))
        rows[CampoDivergenciaInventario.ARMAZENAMENTO] = (
            f'{expected_storage.normalize()} GB',
            f'{detected_storage.quantize(Decimal("0.1"))} GB',
            abs(expected_storage - detected_storage) <= tolerance,
        )

    expected_serial = equipment.numero_serie or equipment.service_tag
    detected_serial = payload.get('numero_serie') or payload.get('serial') or payload.get('service_tag')
    if expected_serial and detected_serial:
        rows[CampoDivergenciaInventario.SERIAL] = (
            str(expected_serial),
            str(detected_serial),
            _normalized_identifier(expected_serial) == _normalized_identifier(detected_serial),
        )

    expected_user = equipment.usuario_windows_esperado
    detected_user = payload.get('logged_user') or payload.get('current_user')
    if expected_user and detected_user:
        rows[CampoDivergenciaInventario.USUARIO] = (
            str(expected_user),
            str(detected_user),
            _normalized_windows_user(expected_user) == _normalized_windows_user(detected_user),
        )

    return rows


def _notify_transition(equipment, divergence, recovered=False):
    if recovered:
        title = f'Divergência resolvida: {equipment.id_patrimonio}'
        message = f'{divergence.get_campo_display()} voltou a coincidir com o cadastro.'
    else:
        title = f'Divergência de inventário: {equipment.id_patrimonio}'
        message = (
            f'{divergence.get_campo_display()}: cadastro {divergence.valor_cadastrado}; '
            f'detectado {divergence.valor_detectado}.'
        )
    link = reverse('detalhe_equipamento', args=[equipment.id_patrimonio])
    transaction.on_commit(lambda: notificar_time_operacional(title, message, link))


def sync_inventory_divergences(equipment, payload, notify=True):
    comparisons = _comparisons(equipment, payload)
    existing = {item.campo: item for item in equipment.divergencias_inventario.all()}
    now = timezone.now()

    for field, (expected, detected, matches) in comparisons.items():
        divergence = existing.get(field)
        if matches:
            if divergence and divergence.ativa:
                divergence.ativa = False
                divergence.resolvida_em = now
                divergence.valor_cadastrado = expected
                divergence.valor_detectado = detected
                divergence.save()
                if notify:
                    _notify_transition(equipment, divergence, recovered=True)
            continue

        if divergence is None:
            divergence = DivergenciaInventario.objects.create(
                equipamento=equipment,
                campo=field,
                valor_cadastrado=expected,
                valor_detectado=detected,
            )
            transitioned = True
        else:
            transitioned = not divergence.ativa
            divergence.valor_cadastrado = expected
            divergence.valor_detectado = detected
            divergence.ativa = True
            divergence.resolvida_em = None
            divergence.save()
        if transitioned and notify:
            _notify_transition(equipment, divergence)

    unchecked_fields = set(existing) - set(comparisons)
    for field in unchecked_fields:
        divergence = existing[field]
        if divergence.ativa:
            divergence.ativa = False
            divergence.resolvida_em = now
            divergence.save(update_fields=['ativa', 'resolvida_em', 'ultima_verificacao_em'])

    return equipment.divergencias_inventario.filter(ativa=True).count()
