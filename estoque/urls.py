from django.urls import path

from .views import estoque_view

urlpatterns = [
    path('', estoque_view, name='estoque'),
]
