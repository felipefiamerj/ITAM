from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def health_view(request):
    checks = {
        'database': _database_check(),
        'cache': _cache_check(),
    }
    healthy = all(check['ok'] for check in checks.values())
    payload = {
        'status': 'ok' if healthy else 'unhealthy',
        'app': getattr(settings, 'APP_SHORT_NAME', 'FIAME'),
        'timestamp': timezone.now().isoformat(),
        'checks': checks,
    }
    status_code = 200 if healthy else 503
    return JsonResponse(payload, status=status_code)


def _database_check():
    try:
        connection = connections['default']
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        return {'ok': False}
    return {'ok': True}


def _cache_check():
    try:
        key = 'itam:health'
        cache.set(key, 'ok', timeout=10)
        if cache.get(key) != 'ok':
            return {'ok': False}
    except Exception:
        return {'ok': False}
    return {'ok': True}
