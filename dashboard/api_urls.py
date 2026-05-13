from django.urls import path

from .views import busca_global_api

urlpatterns = [
    path('busca/', busca_global_api, name='api_busca_global'),
]
