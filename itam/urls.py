import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve

from equipamentos.views import qr_equipamento_publico
from itam.health import health_view
from itam.openapi import openapi_docs_view, openapi_schema_view


def serve_media(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path('', RedirectView.as_view(pattern_name='dashboard', permanent=False), name='home'),
    path('q/<str:id_patrimonio>/', qr_equipamento_publico, name='qr_equipamento_publico_curto'),
    path('health/', health_view, name='health'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('equipamentos/', include('equipamentos.urls')),
    path('chamados/', include('chamados.urls')),
    path('estoque/', include('estoque.urls')),
    path('ia/', include('ia.urls')),
    path('notifications/', include('notifications.urls')),
    path('api/schema/', openapi_schema_view, name='api_schema'),
    path('api/docs/', openapi_docs_view, name='api_docs'),
    path('api/contas/', include('accounts.api_urls')),
    path('api/chamados/', include('chamados.api_urls')),
    path('api/', include('dashboard.api_urls')),
    path('api/', include('equipamentos.api_urls')),
    path('api/estoque/', include('estoque.api_urls')),
]

if settings.MEDIA_URL.startswith('/'):
    urlpatterns.append(
        re_path(rf'^{re.escape(settings.MEDIA_URL.lstrip("/"))}(?P<path>.*)$', serve_media, name='media')
    )

admin.site.site_header = f'{settings.APP_NAME} - Admin'
admin.site.site_title = f'{settings.APP_NAME} Admin'
admin.site.index_title = 'Painel de Administracao'
