from django.urls import path

from .views import busca_view, dashboard_view

urlpatterns = [
    path('', dashboard_view, name='dashboard'),
    path('busca/', busca_view, name='busca_global'),
]
