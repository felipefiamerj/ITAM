from django.contrib import admin

from .models import Chamado, ChamadoFluxoEvento, ChamadoItemSolicitado, TermoAceiteDigital


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


@admin.register(ChamadoFluxoEvento)
class ChamadoFluxoEventoAdmin(admin.ModelAdmin):
    list_display = [
        'chamado',
        'etapa_anterior',
        'etapa_nova',
        'status_anterior',
        'status_novo',
        'usuario',
        'sla_alertado_em',
        'sla_escalado_em',
        'criado_em',
    ]
    list_filter = ['etapa_nova', 'status_novo', 'sla_alertado_em', 'sla_escalado_em', 'criado_em']
    search_fields = ['chamado__titulo', 'chamado__solicitante__matricula', 'chamado__destinatario__matricula', 'observacao']
    raw_id_fields = ['chamado', 'usuario']
    readonly_fields = [
        'chamado',
        'etapa_anterior',
        'etapa_nova',
        'status_anterior',
        'status_novo',
        'usuario',
        'observacao',
        'sla_alertado_em',
        'sla_escalado_em',
        'criado_em',
    ]
    date_hierarchy = 'criado_em'


@admin.register(TermoAceiteDigital)
class TermoAceiteDigitalAdmin(admin.ModelAdmin):
    list_display = ['chamado', 'status', 'nome_assinante', 'matricula_assinante', 'expires_at', 'enviado_em', 'assinado_em', 'documento_hash_curto']
    list_filter = ['status', 'email_enviado', 'expires_at', 'enviado_em', 'assinado_em', 'created_at']
    search_fields = ['chamado__titulo', 'nome_assinante', 'matricula_assinante', 'documento_hash']
    raw_id_fields = ['chamado', 'assinado_por', 'enviado_por']
    readonly_fields = [
        'token',
        'documento_hash',
        'assinatura_data_url',
        'envio_total',
        'email_enviado',
        'email_destino',
        'ip_assinatura',
        'user_agent',
        'enviado_em',
        'assinado_em',
        'created_at',
        'updated_at',
    ]
