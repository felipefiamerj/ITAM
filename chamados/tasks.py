from celery import shared_task

from .services import verificar_sla_chamados


@shared_task(name='chamados.verificar_sla_chamados')
def verificar_sla_chamados_task():
    return verificar_sla_chamados()
