from django.urls import path

from .views import (
    criar_chamado,
    detalhe_chamado,
    editar_chamado,
    entregar_equipamento_chamado,
    excluir_chamado,
    fluxo_chamado_action,
    lista_chamados,
    painel_tecnico,
    termo_chamado_pdf,
    termo_chamado,
)

urlpatterns = [
    path('', lista_chamados, name='chamados'),
    path('painel/', painel_tecnico, name='painel_tecnico'),
    path('novo/', criar_chamado, name='criar_chamado'),
    path('<int:pk>/', detalhe_chamado, name='detalhe_chamado'),
    path('<int:pk>/fluxo/', fluxo_chamado_action, name='fluxo_chamado_action'),
    path('<int:pk>/termo/', termo_chamado, name='termo_chamado'),
    path('<int:pk>/termo/pdf/', termo_chamado_pdf, name='termo_chamado_pdf'),
    path('<int:pk>/editar/', editar_chamado, name='editar_chamado'),
    path('<int:pk>/excluir/', excluir_chamado, name='excluir_chamado'),
    path('<int:pk>/entregar/', entregar_equipamento_chamado, name='entregar_equipamento_chamado'),
]
