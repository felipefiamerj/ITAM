from django.conf import settings


def site_context(request):
    return {
        'app_name': getattr(settings, 'APP_NAME', 'ITAM System'),
        'app_short_name': getattr(settings, 'APP_SHORT_NAME', 'ITAM'),
        'support_email': getattr(settings, 'DEFAULT_FROM_EMAIL', ''),
        'site_url': getattr(settings, 'SITE_URL', ''),
    }
