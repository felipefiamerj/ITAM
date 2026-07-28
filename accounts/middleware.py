from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_redirect(request):
            return redirect('trocar_senha_inicial')
        return self.get_response(request)

    def _should_redirect(self, request):
        if not request.user.is_authenticated:
            return False

        if not getattr(request.user, 'exigir_troca_senha', False):
            return False

        path = request.path_info or '/'
        static_url = settings.STATIC_URL or '/static/'
        media_url = settings.MEDIA_URL or '/media/'
        allowed_paths = {
            reverse('trocar_senha_inicial'),
        }

        if path.startswith((static_url, media_url)):
            return False
        if path in allowed_paths:
            return False

        return not path.rstrip('/').endswith('logout')
