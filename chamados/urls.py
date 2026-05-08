from django.urls import path

from .views import criar_chamado, detalhe_chamado, editar_chamado, lista_chamados

urlpatterns = [
    path('', lista_chamados, name='chamados'),
    path('novo/', criar_chamado, name='criar_chamado'),
    path('<int:pk>/', detalhe_chamado, name='detalhe_chamado'),
    path('<int:pk>/editar/', editar_chamado, name='editar_chamado'),
]
