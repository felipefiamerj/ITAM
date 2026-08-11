from celery import shared_task

from .services import (
    cobrar_assinaturas_pendentes_automaticamente,
    verificar_sla_chamados,
    verificar_sla_etapas_chamados,
)


@shared_task(name='chamados.verificar_sla_chamados')
def verificar_sla_chamados_task():
    return verificar_sla_chamados()


@shared_task(name='chamados.verificar_sla_etapas_chamados')
def verificar_sla_etapas_chamados_task():
    return verificar_sla_etapas_chamados()


@shared_task(name='chamados.cobrar_assinaturas_termos')
def cobrar_assinaturas_termos_task():
    return cobrar_assinaturas_pendentes_automaticamente()
