from django.db.models import Q
from django.utils import timezone

from accounts.models import NivelAcesso, Usuario

from .integrations import enqueue_corporate_notification
from .models import Notification
from .realtime import broadcast_notification


def notificar_usuario(usuario, titulo, mensagem='', link=''):
    if not usuario or not getattr(usuario, 'pk', None):
        return None
    notification = Notification.objects.create(
        user=usuario,
        title=titulo,
        message=mensagem,
        link=link or '',
    )
    broadcast_notification(notification)
    return notification


def notificar_usuarios(usuarios, titulo, mensagem='', link=''):
    enviados = []
    vistos = set()
    agora = timezone.now()

    for usuario in usuarios:
        if not usuario or not getattr(usuario, 'pk', None):
            continue
        if usuario.pk in vistos:
            continue
        vistos.add(usuario.pk)
        enviados.append(
            Notification(
                user=usuario,
                title=titulo,
                message=mensagem,
                link=link or '',
                created_at=agora,
            )
        )

    if enviados:
        Notification.objects.bulk_create(enviados)
        for notification in enviados:
            broadcast_notification(notification)

    return enviados


def usuarios_admins_ativos():
    return Usuario.objects.filter(ativo=True).filter(Q(is_superuser=True) | Q(nivel_acesso=NivelAcesso.ADMIN))


def usuarios_operacionais_ativos():
    return Usuario.objects.filter(ativo=True).filter(
        Q(is_superuser=True)
        | Q(nivel_acesso=NivelAcesso.ADMIN)
        | Q(nivel_acesso=NivelAcesso.ANALISTA)
        | Q(nivel_acesso=NivelAcesso.TECNICO)
    )


def notificar_admins(titulo, mensagem='', link=''):
    notificacoes = notificar_usuarios(usuarios_admins_ativos(), titulo, mensagem, link)
    if notificacoes:
        enqueue_corporate_notification(titulo, mensagem, link, audience='admins')
    return notificacoes


def notificar_time_operacional(titulo, mensagem='', link='', excluir_ids=()):
    usuarios = usuarios_operacionais_ativos()
    ids_excluidos = {usuario_id for usuario_id in excluir_ids if usuario_id}
    if ids_excluidos:
        usuarios = usuarios.exclude(pk__in=ids_excluidos)

    notificacoes = notificar_usuarios(usuarios, titulo, mensagem, link)
    if notificacoes:
        enqueue_corporate_notification(titulo, mensagem, link, audience='operacional')
    return notificacoes
