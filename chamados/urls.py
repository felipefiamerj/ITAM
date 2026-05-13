from django.urls import path

from .views import criar_chamado, detalhe_chamado, editar_chamado, entregar_equipamento_chamado, fluxo_chamado_action, lista_chamados, termo_chamado

urlpatterns = [
    path('', lista_chamados, name='chamados'),
    path('novo/', criar_chamado, name='criar_chamado'),
    path('<int:pk>/', detalhe_chamado, name='detalhe_chamado'),
    path('<int:pk>/fluxo/', fluxo_chamado_action, name='fluxo_chamado_action'),
    path('<int:pk>/termo/', termo_chamado, name='termo_chamado'),
    path('<int:pk>/editar/', editar_chamado, name='editar_chamado'),
    path('<int:pk>/entregar/', entregar_equipamento_chamado, name='entregar_equipamento_chamado'),
]
