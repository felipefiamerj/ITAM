from django.http import JsonResponse
from django.urls import path
from django.utils import timezone

from itam.api_auth import api_auth_required

from .views import auditoria_api_view, build_relatorios_context, busca_global_api


def _serialize_chamado(chamado):
    return {
        'id': chamado.pk,
        'titulo': chamado.titulo,
        'status': chamado.status,
        'status_display': chamado.get_status_display(),
        'fluxo_etapa': chamado.fluxo_etapa,
        'fluxo_etapa_label': chamado.fluxo_etapa_label,
        'prioridade': chamado.prioridade,
        'prioridade_display': chamado.get_prioridade_display(),
        'destinatario': chamado.destinatario_nome_completo,
        'responsavel': chamado.responsavel.nome_completo if chamado.responsavel else None,
        'updated_at': timezone.localtime(chamado.updated_at).strftime('%d/%m/%Y %H:%M'),
    }


def _serialize_atividade(entry):
    return {
        'object_repr': entry.object_repr,
        'actor': entry.actor.nome_completo if entry.actor else None,
        'action': entry.get_action_display(),
        'content_type': entry.content_type.model,
        'timestamp': timezone.localtime(entry.timestamp).strftime('%d/%m/%Y %H:%M'),
    }


@api_auth_required
def relatorios_api_view(request):
    if not request.user.is_operacional:
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)

    context = build_relatorios_context()

    return JsonResponse(
        {
            'relatorios': context['relatorios'],
            'dashboard_charts': context['dashboard_charts'],
            'atividade_recente': [_serialize_atividade(entry) for entry in context['atividade_recente']],
            'chamados_recentes': [_serialize_chamado(chamado) for chamado in context['chamados_recentes']],
        }
    )


urlpatterns = [
    path('busca/', busca_global_api, name='api_busca_global'),
    path('relatorios/', relatorios_api_view, name='api_relatorios'),
    path('auditoria/', auditoria_api_view, name='api_auditoria'),
]
