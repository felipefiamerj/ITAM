from django.utils import timezone

from equipamentos.models import CondicaoEquipamento, Equipamento, StatusEquipamento


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

    return max(0.0, round(score, 2))


def recalcular_scores():
    atualizados = 0
    for equipamento in Equipamento.objects.all():
        novo_score = calcular_score(equipamento)
        if equipamento.score_saude != novo_score:
            equipamento.score_saude = novo_score
            equipamento.save()
            atualizados += 1
    return atualizados


def resumo_monitoramento():
    qs = Equipamento.objects.all()
    return {
        'total': qs.count(),
        'saudaveis': qs.filter(score_saude__gte=80).count(),
        'alerta': qs.filter(score_saude__gte=60, score_saude__lt=80).count(),
        'criticos': qs.filter(score_saude__lt=60).count(),
        'em_manutencao': qs.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
    }
