from celery import shared_task

from .health_service import perform_system_health_checks


@shared_task(name='dashboard.verificar_saude_sistema')
def verificar_saude_sistema_task():
    components = perform_system_health_checks(source='scheduled')
    return {component.component_key: component.status for component in components}
