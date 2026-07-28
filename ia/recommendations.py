from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from chamados.models import Chamado, EtapaFluxoChamado, PrioridadeChamado, StatusChamado
from equipamentos.models import Equipamento, StatusEquipamento

from .config import IA_MODE_DESCRIPTION, IA_MODE_DETAIL, IA_MODE_KEY, IA_MODE_LABEL
from .monitoring import resumo_monitoramento


SOURCE_META = {
    'chamados': {
        'label': 'Chamados',
        'icon': 'fa-ticket-simple',
        'tone': 'rose',
    },
    'equipamentos': {
        'label': 'Equipamentos',
        'icon': 'fa-boxes-stacked',
        'tone': 'blue',
    },
    'monitoramento': {
        'label': 'Monitoramento',
        'icon': 'fa-wave-square',
        'tone': 'teal',
    },
    'governanca': {
        'label': 'Governança',
        'icon': 'fa-user-shield',
        'tone': 'violet',
    },
}

HORIZON_META = {
    'imediato': 'Imediato',
    'hoje': 'Hoje',
    'planejamento': 'Planejamento',
}

HORIZON_ORDER = {
    'imediato': 0,
    'hoje': 1,
    'planejamento': 2,
}

SEVERITY_META = {
    'critical': {
        'label': 'Crítico',
        'badge_class': 'text-bg-danger',
    },
    'warning': {
        'label': 'Atenção',
        'badge_class': 'text-bg-warning',
    },
    'info': {
        'label': 'Planejamento',
        'badge_class': 'text-bg-info',
    },
}


def _format_datetime(value):
    if not value:
        return '-'
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M')


def _format_date(value):
    if not value:
        return '-'
    return value.strftime('%d/%m/%Y')


def _format_hours(value):
    if value <= 0:
        return 'menos de 1h'
    if value == 1:
        return '1h'
    return f'{value}h'


def _build_recommendation(
    *,
    source_key,
    title,
    reason,
    action,
    url,
    horizon,
    severity,
    impact_score,
    meta='',
    subtitle='',
):
    source = SOURCE_META[source_key]
    severity_meta = SEVERITY_META[severity]
    return {
        'source_key': source_key,
        'source_label': source['label'],
        'source_icon': source['icon'],
        'source_tone': source['tone'],
        'title': title,
        'subtitle': subtitle,
        'reason': reason,
        'meta': meta,
        'action': action,
        'url': url,
        'horizon': horizon,
        'horizon_label': HORIZON_META[horizon],
        'severity': severity,
        'severity_label': severity_meta['label'],
        'badge_class': severity_meta['badge_class'],
        'impact_score': impact_score,
    }


def _recommendacao_equipamento(equipamento, now, stale_limit):
    score = float(equipamento.score_saude or 0)
    last_seen = equipamento.last_seen_at
    vida_util = equipamento.vida_util_estimada_meses or 0

    if equipamento.monitoramento_ativo and (not last_seen or last_seen < stale_limit):
        if last_seen:
            atraso_horas = max(1, int((now - last_seen).total_seconds() // 3600))
            reason = f'Heartbeat atrasado há {_format_hours(atraso_horas)}.'
            meta = f'Último sinal em {_format_datetime(last_seen)} · Status {equipamento.get_monitoramento_status_display()}'
            impact_score = 94
        else:
            reason = 'O ativo ainda não recebeu heartbeat do agente.'
            meta = f'Agente {equipamento.last_telemetria_agente.nome if equipamento.last_telemetria_agente else "-"}'
            impact_score = 96

        return _build_recommendation(
            source_key='monitoramento',
            title=f'Verificar {equipamento.id_patrimonio}',
            reason=reason,
            action='Abrir monitoramento',
            url=reverse('detalhe_equipamento', kwargs={'id_patrimonio': equipamento.id_patrimonio}),
            horizon='imediato',
            severity='critical',
            impact_score=impact_score,
            meta=meta,
            subtitle=equipamento.tipo_display,
        )

    if equipamento.status == StatusEquipamento.EM_MANUTENCAO:
        return _build_recommendation(
            source_key='equipamentos',
            title=f'Acompanhar manutenção de {equipamento.id_patrimonio}',
            reason='O ativo já está em manutenção e precisa de acompanhamento do retorno.',
            action='Abrir ativo',
            url=reverse('detalhe_equipamento', kwargs={'id_patrimonio': equipamento.id_patrimonio}),
            horizon='hoje',
            severity='warning',
            impact_score=68,
            meta=f'Última atualização em {_format_datetime(equipamento.updated_at)}',
            subtitle=equipamento.tipo_display,
        )

    if score < 60:
        return _build_recommendation(
            source_key='equipamentos',
            title=f'Programar manutenção para {equipamento.id_patrimonio}',
            reason=f'Score de saúde em {score:.0f}, abaixo do limite seguro para operação.',
            action='Abrir ativo',
            url=reverse('detalhe_equipamento', kwargs={'id_patrimonio': equipamento.id_patrimonio}),
            horizon='imediato',
            severity='critical',
            impact_score=92 - int(score // 2),
            meta=f'Tipo {equipamento.tipo_display} · Condição {equipamento.get_condicao_display()}',
            subtitle=f'Score {score:.0f}',
        )

    if score < 80:
        return _build_recommendation(
            source_key='equipamentos',
            title=f'Revisar {equipamento.id_patrimonio}',
            reason=f'Score de saúde em {score:.0f}, pedindo revisão preventiva.',
            action='Abrir ativo',
            url=reverse('detalhe_equipamento', kwargs={'id_patrimonio': equipamento.id_patrimonio}),
            horizon='hoje',
            severity='warning',
            impact_score=70 - int(score // 6),
            meta=f'Tipo {equipamento.tipo_display} · Condição {equipamento.get_condicao_display()}',
            subtitle=f'Score {score:.0f}',
        )

    if equipamento.garantia_ate and equipamento.garantia_ate < timezone.localdate():
        return _build_recommendation(
            source_key='equipamentos',
            title=f'Planejar substituição de {equipamento.id_patrimonio}',
            reason=f'Garantia vencida em {_format_date(equipamento.garantia_ate)}.',
            action='Abrir ativo',
            url=reverse('detalhe_equipamento', kwargs={'id_patrimonio': equipamento.id_patrimonio}),
            horizon='planejamento',
            severity='info',
            impact_score=52,
            meta=f'Vida útil estimada {vida_util} meses',
            subtitle=equipamento.tipo_display,
        )

    if vida_util and equipamento.created_at:
        meses_em_uso = max(0, (now.date() - equipamento.created_at.date()).days // 30)
        limite_preventivo = max(1, int(vida_util * 0.8))
        if meses_em_uso >= limite_preventivo:
            return _build_recommendation(
                source_key='equipamentos',
                title=f'Reavaliar ciclo de vida de {equipamento.id_patrimonio}',
                reason=f'Ativo com aproximadamente {meses_em_uso} meses em uso e perto do limite previsto.',
                action='Abrir ativo',
                url=reverse('detalhe_equipamento', kwargs={'id_patrimonio': equipamento.id_patrimonio}),
                horizon='planejamento',
                severity='info',
                impact_score=48,
                meta=f'Vida útil estimada {vida_util} meses',
                subtitle=equipamento.tipo_display,
            )

    return None


def _recommendacao_chamado(chamado, now):
    idade_horas = max(0, int((now - chamado.created_at).total_seconds() // 3600))
    prioridade = chamado.prioridade
    fluxo = chamado.fluxo_etapa_atual

    if prioridade == PrioridadeChamado.CRITICA:
        return _build_recommendation(
            source_key='chamados',
            title=f'Atender {chamado.titulo}',
            reason='Chamado classificado como crítico e precisa sair da fila agora.',
            action='Abrir chamado',
            url=reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            horizon='imediato',
            severity='critical',
            impact_score=98 if not chamado.responsavel_id else 94,
            meta=(
                f'#{chamado.pk} · {chamado.get_prioridade_display()} · {chamado.get_status_display()} · '
                f'Colaborador {chamado.destinatario_nome_completo}'
            ),
            subtitle=chamado.fluxo_etapa_label,
        )

    if fluxo == EtapaFluxoChamado.AGUARDANDO_APROVACAO:
        return _build_recommendation(
            source_key='chamados',
            title=f'Cobrar aprovação de {chamado.titulo}',
            reason='O fluxo está travado aguardando retorno do colaborador.',
            action='Abrir chamado',
            url=reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            horizon='hoje',
            severity='warning',
            impact_score=82,
            meta=(
                f'#{chamado.pk} · {chamado.get_status_display()} · '
                f'Aprovado por {chamado.aprovado_por_label}'
            ),
            subtitle=chamado.fluxo_etapa_label,
        )

    if fluxo == EtapaFluxoChamado.AGUARDANDO_ESTOQUE:
        return _build_recommendation(
            source_key='chamados',
            title=f'Liberar estoque para {chamado.titulo}',
            reason='A solicitação depende de disponibilidade antes de seguir no fluxo.',
            action='Abrir chamado',
            url=reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            horizon='hoje',
            severity='warning',
            impact_score=80,
            meta=(
                f'#{chamado.pk} · {chamado.get_status_display()} · '
                f'Atualizado em {_format_datetime(chamado.updated_at)}'
            ),
            subtitle=chamado.fluxo_etapa_label,
        )

    if not chamado.responsavel_id and chamado.status != StatusChamado.ENCERRADO:
        severity = 'critical' if prioridade in {PrioridadeChamado.ALTA, PrioridadeChamado.CRITICA} else 'warning'
        horizon = 'imediato' if prioridade in {PrioridadeChamado.ALTA, PrioridadeChamado.CRITICA} else 'hoje'
        impact_score = 92 if prioridade in {PrioridadeChamado.ALTA, PrioridadeChamado.CRITICA} else 74
        return _build_recommendation(
            source_key='chamados',
            title=f'Assumir {chamado.titulo}',
            reason='O chamado está sem responsável definido.',
            action='Abrir chamado',
            url=reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            horizon=horizon,
            severity=severity,
            impact_score=impact_score,
            meta=(
                f'#{chamado.pk} · {chamado.get_prioridade_display()} · '
                f'Na fila há {_format_hours(idade_horas)}'
            ),
            subtitle=chamado.fluxo_etapa_label,
        )

    if prioridade == PrioridadeChamado.ALTA and chamado.status != StatusChamado.ENCERRADO:
        return _build_recommendation(
            source_key='chamados',
            title=f'Manter {chamado.titulo} em acompanhamento',
            reason='Chamado de alta prioridade merece acompanhamento no mesmo turno.',
            action='Abrir chamado',
            url=reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            horizon='hoje',
            severity='warning',
            impact_score=76,
            meta=(
                f'#{chamado.pk} · {chamado.get_status_display()} · '
                f'Atualizado em {_format_datetime(chamado.updated_at)}'
            ),
            subtitle=chamado.fluxo_etapa_label,
        )

    if chamado.status == StatusChamado.FILA and idade_horas >= 24:
        return _build_recommendation(
            source_key='chamados',
            title=f'Reavaliar fila de {chamado.titulo}',
            reason='O chamado está há tempo demais na fila sem evolução.',
            action='Abrir chamado',
            url=reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            horizon='hoje',
            severity='warning',
            impact_score=72,
            meta=f'#{chamado.pk} · Na fila há {_format_hours(idade_horas)}',
            subtitle=chamado.fluxo_etapa_label,
        )

    if chamado.status == StatusChamado.EM_ATENDIMENTO and idade_horas >= 24:
        return _build_recommendation(
            source_key='chamados',
            title=f'Checar andamento de {chamado.titulo}',
            reason='Chamado em atendimento há bastante tempo e pede uma leitura rápida.',
            action='Abrir chamado',
            url=reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
            horizon='planejamento',
            severity='info',
            impact_score=58,
            meta=f'#{chamado.pk} · Em atendimento há {_format_hours(idade_horas)}',
            subtitle=chamado.fluxo_etapa_label,
        )

    return None


def _recommendacao_governanca(usuario):
    tempo = _format_datetime(usuario.created_at)
    gestor = usuario.gestor.nome_completo if usuario.gestor else 'sem gestor definido'
    return _build_recommendation(
        source_key='governanca',
        title=f'Aprovar {usuario.nome_completo}',
        reason='Existe uma solicitação de acesso aguardando liberação.',
        action='Abrir aprovações',
        url=reverse('usuarios_pendentes'),
        horizon='hoje',
        severity='warning',
        impact_score=78,
        meta=f'Matrícula {usuario.matricula} · Solicitação criada em {tempo} · Gestor {gestor}',
        subtitle=usuario.papel_fluxo,
    )


def _operational_index(recommendacoes):
    if not recommendacoes:
        return 100

    immediate = sum(1 for item in recommendacoes if item['horizon'] == 'imediato')
    today = sum(1 for item in recommendacoes if item['horizon'] == 'hoje')
    planning = sum(1 for item in recommendacoes if item['horizon'] == 'planejamento')
    penalty = (immediate * 14) + (today * 7) + (planning * 3)
    return max(0, min(100, 100 - penalty))


def _operational_state(index):
    if index >= 85:
        return 'Estável'
    if index >= 70:
        return 'Atenção'
    return 'Crítico'


def build_operational_copilot(user):
    now = timezone.now()
    stale_limit = now - timedelta(minutes=getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10))
    monitoring_summary = resumo_monitoramento()

    recommendations = []

    equipamentos = (
        Equipamento.objects.select_related('responsavel', 'last_telemetria_agente')
        .exclude(status=StatusEquipamento.DESCARTADO)
        .order_by('score_saude', '-updated_at', 'id_patrimonio')
    )
    for equipamento in equipamentos[:30]:
        recommendation = _recommendacao_equipamento(equipamento, now, stale_limit)
        if recommendation:
            recommendations.append(recommendation)

    chamados = (
        Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel')
        .exclude(status=StatusChamado.ENCERRADO)
        .order_by('-updated_at', '-created_at', 'pk')
    )
    for chamado in chamados[:40]:
        recommendation = _recommendacao_chamado(chamado, now)
        if recommendation:
            recommendations.append(recommendation)

    usuarios_pendentes = 0
    if user.is_admin:
        pendentes = Usuario.objects.filter(solicitacao_pendente=True).select_related('gestor', 'aprovado_por')
        usuarios_pendentes = pendentes.count()
        recommendations.extend(_recommendacao_governanca(usuario) for usuario in pendentes.order_by('created_at')[:10])

    recommendations.sort(
        key=lambda item: (
            -item['impact_score'],
            HORIZON_ORDER[item['horizon']],
            item['source_label'],
            item['title'],
        )
    )

    source_counts = Counter(item['source_key'] for item in recommendations)
    horizon_counts = Counter(item['horizon'] for item in recommendations)
    top_recommendation = recommendations[0] if recommendations else None
    operational_index = _operational_index(recommendations)
    state_label = _operational_state(operational_index)
    state_caption = {
        'Estável': 'A operação está respirando bem hoje.',
        'Atenção': 'Há pontos de atenção que merecem resposta no turno.',
        'Crítico': 'O fluxo precisa de foco imediato para evitar efeito cascata.',
    }[state_label]

    if top_recommendation:
        primary_text = f'{top_recommendation["action"]} em {top_recommendation["title"]}. {top_recommendation["reason"]}'
    else:
        primary_text = 'Nenhuma ação crítica agora. Continue usando o monitoramento para antecipar movimentos.'

    metrics = [
        {
            'label': 'Índice operacional',
            'value': f'{operational_index}/100',
            'caption': state_label,
            'icon': 'fa-gauge-high',
            'tone': 'slate',
        },
        {
            'label': 'Ações imediatas',
            'value': horizon_counts.get('imediato', 0),
            'caption': 'Exigem reação agora',
            'icon': 'fa-bolt',
            'tone': 'rose',
        },
        {
            'label': 'Ações de hoje',
            'value': horizon_counts.get('hoje', 0),
            'caption': 'Devem sair da fila hoje',
            'icon': 'fa-clock',
            'tone': 'amber',
        },
        {
            'label': 'Planejamento',
            'value': horizon_counts.get('planejamento', 0),
            'caption': 'Podem ser programadas',
            'icon': 'fa-route',
            'tone': 'violet',
        },
        {
            'label': 'Equipamentos em risco',
            'value': source_counts.get('equipamentos', 0) + source_counts.get('monitoramento', 0),
            'caption': 'Score baixo ou telemetria fora',
            'icon': 'fa-boxes-stacked',
            'tone': 'blue',
        },
        {
            'label': 'Chamados críticos',
            'value': sum(1 for item in recommendations if item['source_key'] == 'chamados' and item['severity'] == 'critical'),
            'caption': 'Solicitações que pedem triagem',
            'icon': 'fa-ticket-simple',
            'tone': 'rose',
        },
    ]

    if user.is_admin:
        metrics.append(
            {
                'label': 'Aprovações pendentes',
                'value': usuarios_pendentes,
                'caption': 'Governança do acesso',
                'icon': 'fa-user-clock',
                'tone': 'teal',
            }
        )
    else:
        metrics.append(
            {
                'label': 'Sem heartbeat',
                'value': monitoring_summary['sem_sinal'],
                'caption': 'Ativos sem sinal recente',
                'icon': 'fa-wave-square',
                'tone': 'teal',
            }
        )

    copilot_charts = {
        'recomendacoes_por_origem': {
            'labels': [SOURCE_META[key]['label'] for key in SOURCE_META],
            'values': [source_counts.get(key, 0) for key in SOURCE_META],
        },
        'recomendacoes_por_horizonte': {
            'labels': [HORIZON_META[key] for key in HORIZON_META],
            'values': [horizon_counts.get(key, 0) for key in HORIZON_META],
        },
    }

    return {
        'ia_mode_key': IA_MODE_KEY,
        'ia_mode_label': IA_MODE_LABEL,
        'ia_mode_description': IA_MODE_DESCRIPTION,
        'ia_mode_detail': IA_MODE_DETAIL,
        'index_operacional': operational_index,
        'state_label': state_label,
        'state_caption': state_caption,
        'primary_text': primary_text,
        'primary_recommendation': top_recommendation,
        'recomendacoes': recommendations[:10],
        'recomendacoes_total': len(recommendations),
        'recomendacoes_imediatas': horizon_counts.get('imediato', 0),
        'recomendacoes_hoje': horizon_counts.get('hoje', 0),
        'recomendacoes_planejamento': horizon_counts.get('planejamento', 0),
        'metrics': metrics,
        'copilot_charts': copilot_charts,
        'monitoring_summary': monitoring_summary,
    }
