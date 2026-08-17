from django.contrib import admin

from .models import (
    AgenteMonitoramento,
    AlertaCicloVida,
    DivergenciaInventario,
    EntradaLote,
    Equipamento,
    MovimentacaoEquipamento,
    TelemetriaEvento,
)


class MovimentacaoInline(admin.TabularInline):
    model = MovimentacaoEquipamento
    extra = 0
    readonly_fields = ['created_at']


@admin.register(Equipamento)
class EquipamentoAdmin(admin.ModelAdmin):
    list_display = [
        'id_patrimonio',
        'tipo',
        'status',
        'condicao',
        'monitoramento_status',
        'last_seen_at',
        'responsavel',
        'score_saude',
        'created_at',
    ]
    list_filter = ['tipo', 'status', 'condicao', 'monitoramento_status', 'monitoramento_ativo']
    search_fields = ['id_patrimonio', 'marca', 'modelo', 'service_tag', 'numero_serie']
    raw_id_fields = ['responsavel', 'criado_por']
    inlines = [MovimentacaoInline]


@admin.register(MovimentacaoEquipamento)
class MovimentacaoEquipamentoAdmin(admin.ModelAdmin):
    list_display = ['equipamento', 'tipo', 'realizado_por', 'usuario_novo', 'created_at']
    list_filter = ['tipo', 'created_at']
    search_fields = ['equipamento__id_patrimonio', 'descricao', 'observacoes']
    raw_id_fields = ['equipamento', 'usuario_anterior', 'usuario_novo', 'realizado_por', 'chamado']


@admin.register(EntradaLote)
class EntradaLoteAdmin(admin.ModelAdmin):
    list_display = ['descricao', 'status', 'total_itens', 'itens_importados', 'itens_com_erro', 'criado_por', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['descricao', 'log_erros']


@admin.register(AgenteMonitoramento)
class AgenteMonitoramentoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'host_name', 'ativo', 'last_seen_at', 'last_ip', 'criado_por']
    list_filter = ['ativo', 'created_at', 'updated_at']
    search_fields = ['nome', 'host_name', 'last_ip', 'token']
    raw_id_fields = ['criado_por']
    readonly_fields = ['token', 'last_seen_at', 'last_ip', 'created_at', 'updated_at']


@admin.register(TelemetriaEvento)
class TelemetriaEventoAdmin(admin.ModelAdmin):
    list_display = ['equipamento', 'agente', 'tipo', 'severidade', 'created_at']
    list_filter = ['tipo', 'severidade', 'created_at']
    search_fields = ['equipamento__id_patrimonio', 'mensagem', 'agente__nome', 'agente__host_name']
    raw_id_fields = ['equipamento', 'agente']


@admin.register(AlertaCicloVida)
class AlertaCicloVidaAdmin(admin.ModelAdmin):
    list_display = ['equipamento', 'tipo', 'severidade', 'ativo', 'atualizado_em', 'resolvido_em']
    list_filter = ['ativo', 'severidade', 'tipo']
    search_fields = ['equipamento__id_patrimonio', 'titulo', 'descricao']
    raw_id_fields = ['equipamento']


@admin.register(DivergenciaInventario)
class DivergenciaInventarioAdmin(admin.ModelAdmin):
    list_display = ['equipamento', 'campo', 'valor_cadastrado', 'valor_detectado', 'ativa', 'ultima_verificacao_em']
    list_filter = ['ativa', 'campo']
    search_fields = ['equipamento__id_patrimonio', 'valor_cadastrado', 'valor_detectado']
    raw_id_fields = ['equipamento']
