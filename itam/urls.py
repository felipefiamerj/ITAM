from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='dashboard', permanent=False), name='home'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('equipamentos/', include('equipamentos.urls')),
    path('chamados/', include('chamados.urls')),
    path('estoque/', include('estoque.urls')),
    path('ia/', include('ia.urls')),
    path('notifications/', include('notifications.urls')),
    path('api/', include('dashboard.api_urls')),
    path('api/', include('equipamentos.api_urls')),
    path('api/estoque/', include('estoque.api_urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = 'ITAM System - Admin'
admin.site.site_title = 'ITAM Admin'
admin.site.index_title = 'Painel de Administracao'
