from django.contrib import admin

from .models import Chamado, ChamadoItemSolicitado


@admin.register(Chamado)
class ChamadoAdmin(admin.ModelAdmin):
    list_display = [
        'titulo',
        'servico_realizado_label',
        'tipo_solicitado',
        'itens_total',
        'status',
        'fluxo_etapa_label',
        'prioridade',
        'solicitante',
        'destinatario',
        'responsavel',
        'aprovado_por',
        'created_at',
    ]
    list_filter = ['status', 'fluxo_etapa', 'prioridade', 'servico_realizado', 'tipo_equipamento_solicitado', 'created_at']
    search_fields = [
        'titulo',
        'descricao',
        'servico_realizado',
        'tipo_equipamento_solicitado',
        'itens_solicitados__tipo_equipamento',
        'itens_solicitados__tipo_outro',
        'solicitante__matricula',
        'solicitante__first_name',
        'destinatario__matricula',
        'destinatario__first_name',
        'destinatario__last_name',
        'responsavel__matricula',
        'aprovado_por__matricula',
    ]
    raw_id_fields = ['equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por']
    date_hierarchy = 'created_at'

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('itens_solicitados')

    @admin.display(description='Serviço', ordering='servico_realizado')
    def servico_realizado_label(self, obj):
        if not obj.servico_realizado:
            return '-'
        return obj.get_servico_realizado_display()

    @admin.display(description='Itens solicitados', ordering='tipo_equipamento_solicitado')
    def tipo_solicitado(self, obj):
        return obj.itens_solicitados_resumo

    @admin.display(description='Itens')
    def itens_total(self, obj):
        return obj.itens_solicitados.count()

    @admin.display(description='Fluxo')
    def fluxo_etapa_label(self, obj):
        return obj.fluxo_etapa_label


@admin.register(ChamadoItemSolicitado)
class ChamadoItemSolicitadoAdmin(admin.ModelAdmin):
    list_display = ['chamado', 'tipo_display_admin', 'quantidade', 'observacao', 'created_at']
    search_fields = ['chamado__titulo', 'tipo_equipamento', 'tipo_outro', 'observacao']
    raw_id_fields = ['chamado']
    date_hierarchy = 'created_at'

    @admin.display(description='Tipo')
    def tipo_display_admin(self, obj):
        return obj.tipo_display
