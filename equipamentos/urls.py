from django.urls import path

from .views import criar_equipamento, detalhe_equipamento, editar_equipamento, lista_equipamentos, registrar_movimentacao

urlpatterns = [
    path('', lista_equipamentos, name='equipamentos'),
    path('novo/', criar_equipamento, name='criar_equipamento'),
    path('<str:id_patrimonio>/', detalhe_equipamento, name='detalhe_equipamento'),
    path('<str:id_patrimonio>/editar/', editar_equipamento, name='editar_equipamento'),
    path('<str:id_patrimonio>/movimentar/', registrar_movimentacao, name='registrar_movimentacao'),
]
