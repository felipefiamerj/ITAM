from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from itam.health import health_view
from itam.openapi import openapi_docs_view, openapi_schema_view

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='dashboard', permanent=False), name='home'),
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
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]

admin.site.site_header = f'{settings.APP_NAME} - Admin'
admin.site.site_title = f'{settings.APP_NAME} Admin'
admin.site.index_title = 'Painel de Administracao'
