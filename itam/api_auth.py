from functools import wraps
from secrets import compare_digest

from django.conf import settings
from django.http import JsonResponse

from accounts.models import Usuario


def _shared_api_key():
    return (getattr(settings, 'ITAM_API_SHARED_KEY', '') or '').strip()


def _service_user():
    matricula = (getattr(settings, 'ITAM_API_SERVICE_MATRICULA', '') or '').strip()
    if not matricula:
        return None

    return Usuario.objects.filter(
        matricula__iexact=matricula,
        ativo=True,
        solicitacao_pendente=False,
    ).first()


def _extract_api_key(request):
    header_key = (request.headers.get('X-ITAM-API-Key') or '').strip()
    if header_key:
        return header_key

    authorization = (request.headers.get('Authorization') or '').strip()
    if authorization.lower().startswith('bearer '):
        return authorization[7:].strip()
    return ''


def attach_api_user(request):
    if request.user.is_authenticated:
        return request.user

    expected_key = _shared_api_key()
    if not expected_key:
        return None

    candidate = _extract_api_key(request)
    if not candidate or not compare_digest(candidate, expected_key):
        return None

    usuario = _service_user()
    if not usuario:
        return None

    request.user = usuario
    request.itam_api_client = True
    return usuario


def api_auth_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)

        if attach_api_user(request):
            return view_func(request, *args, **kwargs)

        return JsonResponse({'detail': 'Acesso negado.'}, status=403)

    return _wrapped
