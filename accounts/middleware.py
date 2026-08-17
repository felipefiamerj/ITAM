from django.conf import settings
from django.contrib.auth import logout
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


class ForceAdminTwoFactorSetupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect_response = self._redirect_response(request)
        if redirect_response:
            return redirect_response
        return self.get_response(request)

    def _redirect_response(self, request):
        user = request.user
        if not user.is_authenticated or not getattr(user, 'is_admin', False):
            return None
        if not getattr(settings, 'ITAM_ADMIN_2FA_REQUIRED', True):
            return None
        if getattr(user, 'exigir_troca_senha', False):
            return None

        path = request.path_info or '/'
        allowed_paths = {
            reverse('two_factor_setup'),
            reverse('two_factor_recovery_codes'),
            reverse('logout'),
            reverse('trocar_senha_inicial'),
        }
        if path.startswith((settings.STATIC_URL or '/static/', settings.MEDIA_URL or '/media/')):
            return None
        if path in allowed_paths:
            return None

        if not user.two_factor_enabled:
            return redirect('two_factor_setup')
        if request.session.get('two_factor_verified_user_id') != user.pk:
            logout(request)
            return redirect(f'{reverse("login")}?next={path}')
        return None
