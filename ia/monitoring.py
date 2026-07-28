from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from equipamentos.models import CondicaoEquipamento, Equipamento, StatusEquipamento, StatusMonitoramento
from .config import IA_MODE_DESCRIPTION, IA_MODE_DETAIL, IA_MODE_KEY, IA_MODE_LABEL


def calcular_score(equipamento):
    score = 100.0

    if equipamento.status == StatusEquipamento.EM_MANUTENCAO:
        score -= 30
    elif equipamento.status == StatusEquipamento.DESCARTADO:
        score -= 80

    if equipamento.condicao == CondicaoEquipamento.REGULAR:
        score -= 15
    elif equipamento.condicao == CondicaoEquipamento.RUIM:
        score -= 35
    elif equipamento.condicao == CondicaoEquipamento.INUTIL:
        score -= 60

    if equipamento.garantia_ate and equipamento.garantia_ate < timezone.localdate():
        score -= 10

    if equipamento.vida_util_estimada_meses and equipamento.created_at:
        meses_em_uso = max(0, (timezone.now().date() - equipamento.created_at.date()).days // 30)
        if meses_em_uso > equipamento.vida_util_estimada_meses:
            score -= 20

    if equipamento.monitoramento_ativo:
        limite = timezone.now() - timedelta(minutes=getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10))
        if not equipamento.last_seen_at:
            score -= 10
        elif equipamento.last_seen_at < limite:
            score -= 25

        if equipamento.monitoramento_status == StatusMonitoramento.ALERTA:
            score -= 10
        elif equipamento.monitoramento_status == StatusMonitoramento.OFFLINE:
            score -= 30

    return max(0.0, round(score, 2))


def recalcular_scores():
    atualizados = 0
    for equipamento in Equipamento.objects.all():
        novo_score = calcular_score(equipamento)
        if equipamento.score_saude != novo_score:
            equipamento.score_saude = novo_score
            equipamento.save(update_fields=['score_saude'])
            atualizados += 1
    return atualizados


def resumo_monitoramento():
    qs = Equipamento.objects.all()
    monitorados = qs.filter(monitoramento_ativo=True)
    limite = timezone.now() - timedelta(minutes=getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10))
    return {
        'ia_mode_key': IA_MODE_KEY,
        'ia_mode_label': IA_MODE_LABEL,
        'ia_mode_description': IA_MODE_DESCRIPTION,
        'ia_mode_detail': IA_MODE_DETAIL,
        'total': qs.count(),
        'saudaveis': qs.filter(score_saude__gte=80).count(),
        'alerta': qs.filter(score_saude__gte=60, score_saude__lt=80).count(),
        'criticos': qs.filter(score_saude__lt=60).count(),
        'em_manutencao': qs.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
        'monitorados': monitorados.count(),
        'sem_sinal': monitorados.filter(Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=limite)).count(),
        'telemetria_online': monitorados.filter(last_seen_at__gte=limite, monitoramento_status=StatusMonitoramento.ONLINE).count(),
        'telemetria_alerta': monitorados.filter(monitoramento_status=StatusMonitoramento.ALERTA).count(),
    }
