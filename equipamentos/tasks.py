from celery import shared_task

from equipamentos.telemetria import marcar_equipamentos_sem_sinal
from ia.monitoring import recalcular_scores


@shared_task(name='equipamentos.recalcular_scores')
def recalcular_scores_task():
    return recalcular_scores()


@shared_task(name='equipamentos.verificar_monitoramento')
def verificar_monitoramento_task():
    return marcar_equipamentos_sem_sinal()
