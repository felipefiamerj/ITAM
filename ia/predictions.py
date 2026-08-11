from datetime import timedelta
from math import ceil

from django.conf import settings
from django.db.models import Count, Sum
from django.urls import reverse
from django.utils import timezone

from chamados.models import (
    Chamado,
    ChamadoItemSolicitado,
    EtapaFluxoChamado,
    PrioridadeChamado,
    StatusChamado,
)
from equipamentos.models import Equipamento, StatusEquipamento, TipoEquipamento
from estoque.models import ReservaEstoque, StatusReservaEstoque

PRIORITY_WEIGHTS = {
    PrioridadeChamado.CRITICA: 30,
    PrioridadeChamado.ALTA: 20,
    PrioridadeChamado.MEDIA: 10,
    PrioridadeChamado.BAIXA: 4,
}

STAGE_WEIGHTS = {
    EtapaFluxoChamado.AGUARDANDO_ESTOQUE: 18,
    EtapaFluxoChamado.AGUARDANDO_APROVACAO: 12,
    EtapaFluxoChamado.EM_SEPARACAO: 8,
    EtapaFluxoChamado.PRONTO_PARA_ENTREGA: 6,
}


def build_predictive_insights(*, forecast_days=None, history_days=90, sla_limit=6):
    forecast_days = int(forecast_days or getattr(settings, 'ITAM_PREVISAO_DIAS', 30) or 30)
    demand_forecast = prever_demanda_estoque(forecast_days=forecast_days, history_days=history_days)
    sla_risk = prever_risco_sla(limit=sla_limit)
    return {
        'forecast_days': forecast_days,
        'history_days': history_days,
        'demand_forecast': demand_forecast[:6],
        'sla_risk': sla_risk,
        'metrics': _prediction_metrics(demand_forecast, sla_risk),
    }


def prever_demanda_estoque(*, forecast_days=30, history_days=90):
    now = timezone.now()
    since = now - timedelta(days=history_days)
    effective_days = max(1, (now.date() - since.date()).days)
    tipo_labels = dict(TipoEquipamento.choices)

    demand_rows = (
        ChamadoItemSolicitado.objects.filter(created_at__gte=since)
        .exclude(tipo_equipamento=TipoEquipamento.OUTRO)
        .values('tipo_equipamento')
        .annotate(total=Sum('quantidade'), chamados=Count('chamado_id', distinct=True))
    )
    demand_by_type = {
        row['tipo_equipamento']: {
            'total': int(row['total'] or 0),
            'chamados': int(row['chamados'] or 0),
        }
        for row in demand_rows
    }
    stock_by_type = _count_by_key(
        Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE).values('tipo').annotate(total=Count('id')),
        'tipo',
    )
    reserved_by_type = _count_by_key(
        ReservaEstoque.objects.filter(status__in=[StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA])
        .values('equipamento__tipo')
        .annotate(total=Count('id')),
        'equipamento__tipo',
    )

    tipos = set(demand_by_type) | set(stock_by_type) | set(reserved_by_type)
    forecasts = []
    for tipo in tipos:
        demanda = demand_by_type.get(tipo, {'total': 0, 'chamados': 0})
        consumo_diario = demanda['total'] / effective_days
        previsto = int(ceil(consumo_diario * forecast_days)) if consumo_diario > 0 else 0
        em_estoque = stock_by_type.get(tipo, 0)
        reservados = reserved_by_type.get(tipo, 0)
        cobertura = em_estoque - previsto
        cobertura_dias = round(em_estoque / consumo_diario, 1) if consumo_diario > 0 else None
        risco = _stock_risk_score(previsto=previsto, em_estoque=em_estoque, cobertura_dias=cobertura_dias, forecast_days=forecast_days)

        forecasts.append(
            {
                'tipo': tipo,
                'label': tipo_labels.get(tipo, tipo),
                'historico_total': demanda['total'],
                'historico_chamados': demanda['chamados'],
                'media_diaria': round(consumo_diario, 2),
                'previsao_periodo': previsto,
                'em_estoque': em_estoque,
                'reservados': reservados,
                'cobertura': cobertura,
                'cobertura_dias': cobertura_dias,
                'risk_score': risco,
                'risk_label': _risk_label(risco),
                'badge_class': _badge_class(risco),
            }
        )

    return sorted(
        forecasts,
        key=lambda item: (
            -item['risk_score'],
            -item['previsao_periodo'],
            item['label'],
        ),
    )


def prever_risco_sla(*, limit=6):
    chamados = (
        Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel')
        .exclude(status=StatusChamado.ENCERRADO)
        .order_by('created_at')[:60]
    )
    scored = []
    now = timezone.now()
    for chamado in chamados:
        total_minutes = max(1, chamado.sla_duracao_minutos)
        elapsed_minutes = max(0, int((now - chamado.created_at).total_seconds() // 60))
        elapsed_ratio = elapsed_minutes / total_minutes
        risk = min(99, int((elapsed_ratio * 60) + PRIORITY_WEIGHTS.get(chamado.prioridade, 8)))

        if not chamado.responsavel_id:
            risk += 12
        risk += STAGE_WEIGHTS.get(chamado.fluxo_etapa_atual, 0)
        if chamado.sla_em_atraso:
            risk = max(risk, 95)
        risk = max(0, min(99, risk))

        scored.append(
            {
                'id': chamado.pk,
                'titulo': chamado.titulo,
                'destinatario': chamado.destinatario_nome_completo,
                'responsavel': chamado.responsavel.nome_completo if chamado.responsavel else 'Sem responsavel',
                'prioridade': chamado.get_prioridade_display(),
                'status': chamado.get_status_display(),
                'fluxo_etapa': chamado.fluxo_etapa_label,
                'sla_restante': chamado.sla_restante_label,
                'risk_score': risk,
                'risk_label': _risk_label(risk),
                'badge_class': _badge_class(risk),
                'url': reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            }
        )

    return sorted(scored, key=lambda item: (-item['risk_score'], item['id']))[:limit]


def _count_by_key(rows, key):
    return {row[key]: int(row['total'] or 0) for row in rows if row[key]}


def _stock_risk_score(*, previsto, em_estoque, cobertura_dias, forecast_days):
    if previsto <= 0:
        return 20 if em_estoque == 0 else 5
    if em_estoque <= 0:
        return 98
    if em_estoque < previsto:
        return 88
    if cobertura_dias is not None and cobertura_dias <= max(1, forecast_days * 0.5):
        return 72
    if previsto >= max(1, int(em_estoque * 0.75)):
        return 58
    return 28


def _risk_label(score):
    if score >= 85:
        return 'Critico'
    if score >= 60:
        return 'Atencao'
    return 'Estavel'


def _badge_class(score):
    if score >= 85:
        return 'text-bg-danger'
    if score >= 60:
        return 'text-bg-warning'
    return 'text-bg-success'


def _prediction_metrics(demand_forecast, sla_risk):
    rupture_risk = sum(1 for item in demand_forecast if item['risk_score'] >= 60)
    sla_critical = sum(1 for item in sla_risk if item['risk_score'] >= 85)
    top_demand = demand_forecast[0] if demand_forecast else None
    top_sla = sla_risk[0] if sla_risk else None
    return {
        'rupture_risk': rupture_risk,
        'sla_critical': sla_critical,
        'top_demand': top_demand,
        'top_sla': top_sla,
    }
