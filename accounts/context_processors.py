from .models import Usuario


def accounts_context(request):
    if not request.user.is_authenticated or not request.user.is_admin:
        return {
            'accounts_pending_count': 0,
            'accounts_pending_recent': [],
        }

    pending_qs = Usuario.objects.filter(solicitacao_pendente=True).select_related('gestor').order_by('created_at')
    return {
        'accounts_pending_count': pending_qs.count(),
        'accounts_pending_recent': pending_qs[:5],
    }
