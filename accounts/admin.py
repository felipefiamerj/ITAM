from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .forms import UsuarioCreateForm, UsuarioUpdateForm
from .models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    form = UsuarioUpdateForm
    add_form = UsuarioCreateForm
    list_display = ['matricula', 'get_full_name', 'nivel_acesso', 'status_acesso', 'site', 'setor', 'ativo']
    list_filter = ['nivel_acesso', 'site', 'ativo', 'solicitacao_pendente', 'exigir_troca_senha', 'is_superuser']
    search_fields = ['matricula', 'first_name', 'last_name', 'email']
    ordering = ['first_name', 'last_name', 'matricula']
    readonly_fields = ['last_login', 'date_joined', 'aprovado_em', 'aprovado_por', 'created_at', 'updated_at']
    fieldsets = (
        (None, {'fields': ('matricula', 'first_name', 'last_name', 'email', 'foto')}),
        ('Permissões', {'fields': ('nivel_acesso', 'ativo', 'solicitacao_pendente', 'exigir_troca_senha')}),
        ('Organização', {'fields': ('site', 'setor', 'andar_sala', 'gestor', 'contato')}),
        ('Aprovação', {'fields': ('aprovado_em', 'aprovado_por', 'motivo_recusa')}),
        ('Datas', {'fields': ('last_login', 'date_joined', 'created_at', 'updated_at')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('matricula', 'first_name', 'last_name', 'email', 'password1', 'password2')}),
        ('Permissões', {'fields': ('nivel_acesso', 'ativo', 'solicitacao_pendente', 'exigir_troca_senha')}),
        ('Organização', {'fields': ('site', 'setor', 'andar_sala', 'gestor', 'contato')}),
    )
