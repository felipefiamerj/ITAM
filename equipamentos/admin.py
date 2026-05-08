from django.contrib import admin

from .models import EntradaLote, Equipamento, MovimentacaoEquipamento


class MovimentacaoInline(admin.TabularInline):
    model = MovimentacaoEquipamento
    extra = 0
    readonly_fields = ['created_at']


@admin.register(Equipamento)
class EquipamentoAdmin(admin.ModelAdmin):
    list_display = ['id_patrimonio', 'tipo', 'status', 'condicao', 'responsavel', 'score_saude', 'created_at']
    list_filter = ['tipo', 'status', 'condicao']
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
