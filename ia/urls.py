from django.urls import path

from .views import copilot_view, monitoring_view

urlpatterns = [
    path('', copilot_view, name='ia'),
    path('monitoramento/', monitoring_view, name='ia_monitoramento'),
]
