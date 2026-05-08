from django.contrib import admin

from .models import Chamado


@admin.register(Chamado)
class ChamadoAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'status', 'prioridade', 'solicitante', 'responsavel', 'created_at']
    list_filter = ['status', 'prioridade', 'created_at']
    search_fields = ['titulo', 'descricao', 'solicitante__matricula', 'solicitante__first_name', 'responsavel__matricula']
    raw_id_fields = ['equipamento', 'solicitante', 'responsavel']
    date_hierarchy = 'created_at'
