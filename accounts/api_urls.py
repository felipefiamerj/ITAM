from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from django.db.models import Q

from .models import Usuario
from itam.api_auth import api_auth_required


def _serialize_usuario(usuario):
    return {
        'id': usuario.pk,
        'matricula': usuario.matricula,
        'nome_completo': usuario.nome_completo,
        'email': usuario.email,
        'nivel_acesso': usuario.nivel_acesso,
        'nivel_acesso_display': usuario.get_nivel_acesso_display(),
        'papel_fluxo': usuario.papel_fluxo,
        'status_acesso': usuario.status_acesso,
        'ativo': usuario.ativo,
        'solicitacao_pendente': usuario.solicitacao_pendente,
        'site': usuario.site,
        'setor': usuario.setor,
        'andar_sala': usuario.andar_sala,
        'contato': usuario.contato,
        'foto': usuario.foto.url if usuario.foto else None,
        'gestor': usuario.gestor.nome_completo if usuario.gestor else None,
        'aprovado_por': usuario.aprovado_por.nome_completo if usuario.aprovado_por else None,
        'created_at': timezone.localtime(usuario.created_at).strftime('%d/%m/%Y %H:%M'),
        'updated_at': timezone.localtime(usuario.updated_at).strftime('%d/%m/%Y %H:%M'),
    }


@api_auth_required
def me_api(request):
    return JsonResponse(_serialize_usuario(request.user))


@api_auth_required
def usuarios_api(request):
    if not request.user.is_admin:
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)

    q = request.GET.get('q', '').strip()
    qs = Usuario.objects.select_related('gestor', 'aprovado_por').order_by('first_name', 'last_name', 'matricula')
    if q:
        qs = qs.filter(
            Q(matricula__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(site__icontains=q)
            | Q(setor__icontains=q)
        )
    return JsonResponse({'count': qs.count(), 'results': [_serialize_usuario(usuario) for usuario in qs[:50]]})


@api_auth_required
def usuario_api(request, pk):
    usuario = get_object_or_404(Usuario.objects.select_related('gestor', 'aprovado_por'), pk=pk)
    if not request.user.is_admin and usuario.pk != request.user.pk:
        return JsonResponse({'detail': 'Acesso negado.'}, status=403)
    return JsonResponse(_serialize_usuario(usuario))


urlpatterns = [
    path('me/', me_api, name='api_me'),
    path('usuarios/', usuarios_api, name='api_usuarios'),
    path('usuarios/<int:pk>/', usuario_api, name='api_usuario'),
]
