from django.urls import path

from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('recuperar-senha/', views.recuperar_senha, name='recuperar_senha'),
    path('recuperar-senha/<str:uidb64>/<str:token>/', views.redefinir_senha, name='redefinir_senha'),
    path('logout/', views.logout_view, name='logout'),
    path('ativar/<str:uidb64>/<str:token>/', views.ativar_conta, name='ativar_conta'),
    path('usuarios/', views.lista_usuarios, name='lista_usuarios'),
    path('usuarios/pendentes/', views.usuarios_pendentes, name='usuarios_pendentes'),
    path('usuarios/novo/', views.criar_usuario, name='criar_usuario'),
    path('usuarios/<int:pk>/aprovar/', views.aprovar_usuario, name='aprovar_usuario'),
    path('usuarios/<int:pk>/reprovar/', views.reprovar_usuario, name='reprovar_usuario'),
    path('usuarios/<int:pk>/editar/', views.editar_usuario, name='editar_usuario'),
    path('usuarios/<int:pk>/perfil/', views.perfil_usuario, name='perfil_usuario'),
    path('perfil/', views.perfil_usuario, name='meu_perfil'),
    path('trocar-senha-inicial/', views.trocar_senha_inicial, name='trocar_senha_inicial'),
]
