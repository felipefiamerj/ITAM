from django.urls import path

from .views import estoque_view, reserva_inteligente_view

urlpatterns = [
    path('', estoque_view, name='estoque'),
    path('reservas/inteligente/', reserva_inteligente_view, name='reserva_inteligente_estoque'),
]
