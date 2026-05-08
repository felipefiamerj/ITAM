from django.urls import path

from .views import monitoring_view

urlpatterns = [
    path('', monitoring_view, name='ia'),
]
