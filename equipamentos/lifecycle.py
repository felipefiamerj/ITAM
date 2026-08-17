from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from notifications.services import notificar_admins

from .models import (
    AlertaCicloVida,
    CondicaoEquipamento,
    Equipamento,
    StatusEquipamento,
    TipoAlertaCicloVida,
)


def lifecycle_assets_queryset(queryset=None):
    since = timezone.now() - timedelta(days=180)
    decimal_field = DecimalField(max_digits=14, decimal_places=2)
    queryset = queryset if queryset is not None else Equipamento.objects.all()
    return queryset.annotate(
        lifecycle_maintenance_count=Count(
            'movimentacoes',
            filter=Q(movimentacoes__tipo='manutencao', movimentacoes__created_at__gte=since),
            distinct=True,
        ),
        lifecycle_maintenance_cost=Coalesce(
            Sum(
                'movimentacoes__custo_manutencao',
                filter=Q(movimentacoes__custo_manutencao__isnull=False),
            ),
            Value(Decimal('0.00')),
            output_field=decimal_field,
        ),
    )


def desired_lifecycle_alerts(equipment):
    today = timezone.localdate()
    alerts = {}

    if equipment.garantia_ate:
        warranty_days = (equipment.garantia_ate - today).days
        if warranty_days < 0:
            alerts[TipoAlertaCicloVida.GARANTIA] = (
                'warning',
                'Garantia vencida',
                f'Garantia vencida há {abs(warranty_days)} dia(s), em {equipment.garantia_ate:%d/%m/%Y}.',
            )
        elif warranty_days <= 60:
            alerts[TipoAlertaCicloVida.GARANTIA] = (
                'warning',
                'Garantia próxima do vencimento',
                f'Garantia vence em {warranty_days} dia(s), em {equipment.garantia_ate:%d/%m/%Y}.',
            )

    replacement_date = equipment.data_prevista_substituicao
    if replacement_date:
        replacement_days = (replacement_date - today).days
        if replacement_days < 0:
            alerts[TipoAlertaCicloVida.SUBSTITUICAO] = (
                'critical',
                'Substituição atrasada',
                f'Previsão ultrapassada há {abs(replacement_days)} dia(s), em {replacement_date:%d/%m/%Y}.',
            )
        elif replacement_days <= 90:
            alerts[TipoAlertaCicloVida.SUBSTITUICAO] = (
                'warning',
                'Substituição próxima',
                f'Substituição prevista em {replacement_days} dia(s), em {replacement_date:%d/%m/%Y}.',
            )

    maintenance_count = getattr(equipment, 'lifecycle_maintenance_count', None)
    if maintenance_count is None:
        since = timezone.now() - timedelta(days=180)
        maintenance_count = equipment.movimentacoes.filter(tipo='manutencao', created_at__gte=since).count()
    if maintenance_count >= 3:
        alerts[TipoAlertaCicloVida.MANUTENCAO_RECORRENTE] = (
            'critical',
            'Manutenção recorrente',
            f'{maintenance_count} entradas em manutenção nos últimos 180 dias.',
        )

    maintenance_cost = getattr(equipment, 'lifecycle_maintenance_cost', None)
    if maintenance_cost is None:
        maintenance_cost = equipment.custo_acumulado_manutencao
    if equipment.valor_aquisicao and maintenance_cost:
        ratio = (maintenance_cost / equipment.valor_aquisicao) * Decimal('100')
        if ratio >= Decimal('50'):
            severity = 'critical'
        elif ratio >= Decimal('30'):
            severity = 'warning'
        else:
            severity = ''
        if severity:
            alerts[TipoAlertaCicloVida.CUSTO_MANUTENCAO] = (
                severity,
                'Custo de manutenção elevado',
                f'Custos acumulados representam {ratio:.0f}% do valor de aquisição.',
            )

    if equipment.condicao in {CondicaoEquipamento.RUIM, CondicaoEquipamento.INUTIL}:
        alerts[TipoAlertaCicloVida.CONDICAO] = (
            'critical',
            'Condição física crítica',
            f'Equipamento classificado como {equipment.get_condicao_display().lower()}.',
        )

    return alerts


def _notify_alert(alert):
    link = reverse('lifecycle_dashboard')
    notificar_admins(
        f'Ciclo de vida: {alert.equipamento.id_patrimonio}',
        f'{alert.titulo}. {alert.descricao}',
        link,
    )


def _sync_asset(equipment, existing, notify=True):
    now = timezone.now()
    desired = desired_lifecycle_alerts(equipment)
    activated = 0
    resolved = 0

    for alert_type, (severity, title, description) in desired.items():
        alert = existing.get(alert_type)
        if alert is None:
            alert = AlertaCicloVida.objects.create(
                equipamento=equipment,
                tipo=alert_type,
                severidade=severity,
                titulo=title,
                descricao=description,
            )
            existing[alert_type] = alert
            transitioned = True
        else:
            transitioned = not alert.ativo
            alert.severidade = severity
            alert.titulo = title
            alert.descricao = description
            alert.ativo = True
            alert.resolvido_em = None
            alert.save()
        if transitioned:
            activated += 1
            if notify:
                _notify_alert(alert)

    for alert_type, alert in existing.items():
        if alert_type not in desired and alert.ativo:
            alert.ativo = False
            alert.resolvido_em = now
            alert.save(update_fields=['ativo', 'resolvido_em', 'atualizado_em'])
            resolved += 1

    return activated, resolved


def sync_lifecycle_for_equipment(equipment, notify=True):
    annotated = lifecycle_assets_queryset(Equipamento.objects.filter(pk=equipment.pk)).get()
    existing = {alert.tipo: alert for alert in annotated.alertas_ciclo_vida.all()}
    return _sync_asset(annotated, existing, notify=notify)


@transaction.atomic
def sync_lifecycle_alerts(notify=True):
    assets = lifecycle_assets_queryset(
        Equipamento.objects.exclude(status=StatusEquipamento.DESCARTADO)
    ).order_by('pk')
    existing_by_asset = defaultdict(dict)
    for alert in AlertaCicloVida.objects.select_related('equipamento'):
        existing_by_asset[alert.equipamento_id][alert.tipo] = alert

    activated = 0
    resolved = 0
    to_create = []
    to_update = []
    processed_ids = set()
    for equipment in assets.iterator(chunk_size=500):
        processed_ids.add(equipment.pk)
        existing = existing_by_asset[equipment.pk]
        desired = desired_lifecycle_alerts(equipment)
        now = timezone.now()

        for alert_type, (severity, title, description) in desired.items():
            alert = existing.get(alert_type)
            if alert is None:
                to_create.append(
                    AlertaCicloVida(
                        equipamento=equipment,
                        tipo=alert_type,
                        severidade=severity,
                        titulo=title,
                        descricao=description,
                    )
                )
                activated += 1
                continue

            transitioned = not alert.ativo
            changed = transitioned or any(
                [
                    alert.severidade != severity,
                    alert.titulo != title,
                    alert.descricao != description,
                    alert.resolvido_em is not None,
                ]
            )
            if changed:
                alert.severidade = severity
                alert.titulo = title
                alert.descricao = description
                alert.ativo = True
                alert.resolvido_em = None
                alert.atualizado_em = now
                to_update.append(alert)
            if transitioned:
                activated += 1

        for alert_type, alert in existing.items():
            if alert_type not in desired and alert.ativo:
                alert.ativo = False
                alert.resolvido_em = now
                alert.atualizado_em = now
                to_update.append(alert)
                resolved += 1

    for equipment_id, alerts in existing_by_asset.items():
        if equipment_id in processed_ids:
            continue
        for alert in alerts.values():
            if alert.ativo:
                alert.ativo = False
                alert.resolvido_em = timezone.now()
                alert.atualizado_em = timezone.now()
                to_update.append(alert)
                resolved += 1

    if to_create:
        AlertaCicloVida.objects.bulk_create(to_create, batch_size=1000)
    if to_update:
        AlertaCicloVida.objects.bulk_update(
            to_update,
            ['severidade', 'titulo', 'descricao', 'ativo', 'resolvido_em', 'atualizado_em'],
            batch_size=1000,
        )
    if notify and activated:
        notificar_admins(
            'Novos alertas de ciclo de vida',
            f'{activated} alerta(s) aberto(s) após a verificação de {len(processed_ids)} ativo(s).',
            reverse('lifecycle_dashboard'),
        )

    return {'processed': len(processed_ids), 'activated': activated, 'resolved': resolved}
