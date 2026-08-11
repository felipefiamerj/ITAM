import hashlib
import re
from functools import wraps
from secrets import compare_digest

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from accounts.models import Usuario

RATE_RE = re.compile(r'^\s*(?P<count>\d+)\s*/\s*(?P<period>[smhd])\s*$')
PERIOD_SECONDS = {
    's': 1,
    'm': 60,
    'h': 60 * 60,
    'd': 24 * 60 * 60,
}


def _shared_api_key():
    return (getattr(settings, 'ITAM_API_SHARED_KEY', '') or '').strip()


def _shared_api_key_sha256():
    return (getattr(settings, 'ITAM_API_SHARED_KEY_SHA256', '') or '').strip().lower()


def _hash_value(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _api_key_matches(candidate):
    if not candidate:
        return False

    expected_hash = _shared_api_key_sha256()
    if expected_hash and compare_digest(_hash_value(candidate), expected_hash):
        return True

    expected_key = _shared_api_key()
    return bool(expected_key and compare_digest(candidate, expected_key))


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


def _client_ip(request):
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _parse_rate(rate):
    match = RATE_RE.match(str(rate or ''))
    if not match:
        return None
    return int(match.group('count')), PERIOD_SECONDS[match.group('period')]


def _rate_limited(scope, identity, rate):
    parsed = _parse_rate(rate)
    if not parsed:
        return False

    limit, window = parsed
    identity_hash = _hash_value(str(identity))
    key = f'itam:ratelimit:{scope}:{identity_hash}'
    try:
        cache.add(key, 0, timeout=window)
        current = cache.incr(key)
    except Exception:
        return False
    return current > limit


def _rate_limit_response():
    return JsonResponse({'detail': 'Muitas requisicoes. Aguarde alguns instantes e tente novamente.'}, status=429)


def attach_api_user(request):
    if request.user.is_authenticated:
        return request.user

    if not (_shared_api_key() or _shared_api_key_sha256()):
        return None

    candidate = _extract_api_key(request)
    if not _api_key_matches(candidate):
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
        candidate_key = _extract_api_key(request)
        pre_auth_identity = candidate_key or _client_ip(request)
        if _rate_limited('api-auth', pre_auth_identity, getattr(settings, 'ITAM_API_AUTH_RATE_LIMIT', '30/m')):
            return _rate_limit_response()

        usuario = request.user if request.user.is_authenticated else attach_api_user(request)
        if not usuario:
            return JsonResponse({'detail': 'Acesso negado.'}, status=403)

        if getattr(request, 'itam_api_client', False):
            request_identity = f'api-key:{_hash_value(candidate_key)}'
        else:
            request_identity = f'user:{usuario.pk}'
        if _rate_limited('api-requests', request_identity, getattr(settings, 'ITAM_API_REQUEST_RATE_LIMIT', '120/m')):
            return _rate_limit_response()

        return view_func(request, *args, **kwargs)

    return _wrapped
