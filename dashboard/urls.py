from django.urls import path

from .views import (
    asset_health_view,
    backup_configuration_view,
    backup_status_view,
    busca_view,
    dashboard_view,
    relatorios_view,
    restore_status_view,
    system_health_view,
)

urlpatterns = [
    path('', dashboard_view, name='dashboard'),
    path('busca/', busca_view, name='busca_global'),
    path('relatorios/', relatorios_view, name='relatorios'),
    path('administracao/backups/', backup_configuration_view, name='backup_configuration'),
    path('administracao/backups/status/', backup_status_view, name='backup_status'),
    path('administracao/backups/restauracao/<uuid:operation_id>/', restore_status_view, name='restore_status'),
    path('administracao/saude/', system_health_view, name='system_health'),
    path('ativos/saude/', asset_health_view, name='asset_health'),
]
