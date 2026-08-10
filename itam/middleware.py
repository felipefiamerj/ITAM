from django.http import JsonResponse

try:
    from django_ratelimit.exceptions import Ratelimited
except Exception:  # pragma: no cover - keeps management commands importable during partial installs.
    Ratelimited = None


def ratelimited_view(request, exception=None):
    return JsonResponse(
        {'detail': 'Muitas tentativas. Aguarde alguns instantes e tente novamente.'},
        status=429,
    )


class RateLimitResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if Ratelimited is not None and isinstance(exception, Ratelimited):
            return ratelimited_view(request, exception)
        return None
