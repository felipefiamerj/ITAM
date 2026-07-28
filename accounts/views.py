from contextlib import suppress
from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode

from chamados.models import Chamado
from equipamentos.models import Equipamento
from notifications.services import notificar_admins, notificar_usuario

from .forms import (
    LoginForm,
    SolicitacaoAcessoForm,
    SolicitacaoRecuperacaoSenhaForm,
    TrocaSenhaInicialForm,
    UsuarioApprovalForm,
    UsuarioCreateForm,
    UsuarioUpdateForm,
)
from .models import NivelAcesso, Usuario
from .tokens import account_activation_token, password_recovery_token


def _safe_next_url(request, next_url):
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None


def _site_name():
    return getattr(settings, 'APP_NAME', 'ITAM System')


def _absolute_url(request, relative_path):
    site_url = (getattr(settings, 'SITE_URL', '') or '').strip()
    if site_url:
        return urljoin(site_url.rstrip('/') + '/', relative_path.lstrip('/'))
    return request.build_absolute_uri(relative_path)


def _activation_link(request, usuario):
    uidb64 = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = account_activation_token.make_token(usuario)
    return _absolute_url(request, reverse('ativar_conta', kwargs={'uidb64': uidb64, 'token': token}))


def _recovery_link(request, usuario):
    uidb64 = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = password_recovery_token.make_token(usuario)
    return _absolute_url(request, reverse('redefinir_senha', kwargs={'uidb64': uidb64, 'token': token}))


def _usuario_por_uidb64(uidb64):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
    except (TypeError, ValueError, OverflowError):
        return None

    try:
        return Usuario.objects.get(pk=uid)
    except Usuario.DoesNotExist:
        return None


def _home_url_name(user):
    return 'dashboard'


def _redirect_after_login(request, usuario):
    next_url = _safe_next_url(request, request.GET.get('next'))
    if usuario.exigir_troca_senha:
        if next_url:
            request.session['post_password_change_next'] = next_url
        else:
            request.session.pop('post_password_change_next', None)
        return redirect('trocar_senha_inicial')

    request.session.pop('post_password_change_next', None)
    if next_url:
        return redirect(next_url)
    return redirect(_home_url_name(usuario))


def _aplicar_filtros_usuarios(qs, q=None, status=None):
    if q:
        qs = qs.filter(
            Q(matricula__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(site__icontains=q)
            | Q(setor__icontains=q)
            | Q(motivo_recusa__icontains=q)
        )

    if status == 'pendente':
        qs = qs.filter(solicitacao_pendente=True)
    elif status == 'ativo':
        qs = qs.filter(ativo=True, solicitacao_pendente=False)
    elif status == 'inativo':
        qs = qs.filter(ativo=False, solicitacao_pendente=False)
    elif status == 'operacional':
        qs = qs.filter(
            nivel_acesso__in=[NivelAcesso.TECNICO, NivelAcesso.ANALISTA, NivelAcesso.ADMIN],
            solicitacao_pendente=False,
        )
    elif status == 'solicitante':
        qs = qs.filter(nivel_acesso=NivelAcesso.VIEWER, solicitacao_pendente=False)

    return qs


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_home_url_name(request.user))

    form_type = request.POST.get('form_type') if request.method == 'POST' else None
    login_form = LoginForm(
        request,
        data=request.POST if request.method == 'POST' and form_type != 'solicitante' else None,
        prefix='login',
    )
    quick_form = SolicitacaoAcessoForm(
        request.POST if request.method == 'POST' and form_type == 'solicitante' else None,
        prefix='solicitante',
    )

    if request.method == 'POST':
        if form_type == 'solicitante':
            if quick_form.is_valid():
                usuario = quick_form.save()
                notificar_admins(
                    'Nova solicitação de acesso',
                    f'{usuario.nome_completo} ({usuario.matricula}) solicitou acesso mínimo ao sistema.',
                    link=reverse('usuarios_pendentes'),
                )
                messages.success(
                    request,
                    f'Solicitação registrada para {usuario.nome_completo}. '
                    'O cadastro ficou pendente para aprovação do administrador.',
                )
                return redirect('login')
        elif login_form.is_valid():
            usuario = login_form.get_user()
            login(request, usuario)
            messages.success(request, f'Bem-vindo, {usuario.first_name or usuario.matricula}!')
            return _redirect_after_login(request, usuario)
        else:
            matricula = (
                request.POST.get('login-username')
                or request.POST.get('username')
                or request.POST.get('matricula')
                or ''
            ).strip()
            if matricula:
                usuario = Usuario.objects.filter(matricula__iexact=matricula).first()
                if usuario and usuario.solicitacao_pendente:
                    messages.warning(request, 'Seu acesso ainda está aguardando aprovação.')
                elif usuario and not usuario.ativo:
                    messages.warning(request, 'Sua conta está inativa. Procure o administrador.')

    return render(
        request,
        'accounts/login.html',
        {
            'form': login_form,
            'request_form': quick_form,
        },
    )


def recuperar_senha(request):
    if request.user.is_authenticated:
        return redirect(_home_url_name(request.user))

    form = SolicitacaoRecuperacaoSenhaForm(request.POST or None)
    if form.is_valid():
        usuario = form.get_usuario()
        if usuario and usuario.email:
            link_recuperacao = _recovery_link(request, usuario)
            assunto = f'Recuperação de senha no {_site_name()}'
            mensagem = f'''Olá {usuario.first_name or usuario.matricula},

Recebemos uma solicitação para redefinir a senha da sua conta no {_site_name()}.

Para criar uma nova senha, acesse o link abaixo:
{link_recuperacao}

Se você não solicitou essa alteração, ignore esta mensagem.

Atenciosamente,
{_site_name()}
            '''
            with suppress(Exception):
                send_mail(
                    assunto,
                    mensagem,
                    settings.DEFAULT_FROM_EMAIL,
                    [usuario.email],
                    fail_silently=False,
                )

        messages.success(
            request,
            'Se houver uma conta ativa com esse identificador e e-mail cadastrado, você receberá instruções para redefinir a senha.',
        )
        return redirect('login')

    return render(
        request,
        'accounts/recuperar_senha.html',
        {
            'form': form,
        },
    )


def redefinir_senha(request, uidb64, token):
    usuario = _usuario_por_uidb64(uidb64)
    if not usuario or not password_recovery_token.check_token(usuario, token):
        messages.error(request, 'O link de recuperação é inválido ou expirou. Solicite um novo acesso.')
        return redirect('login')

    if request.method == 'POST':
        form = TrocaSenhaInicialForm(usuario, request.POST)
        if form.is_valid():
            usuario = form.save()
            usuario.exigir_troca_senha = False
            usuario.save(update_fields=['exigir_troca_senha', 'updated_at'])
            messages.success(request, 'Senha redefinida com sucesso. Você já pode entrar no sistema.')
            notificar_usuario(
                usuario,
                'Senha redefinida',
                'Sua senha foi atualizada com sucesso no ITAM System.',
                link=reverse('login'),
            )
            return redirect('login')
    else:
        form = TrocaSenhaInicialForm(usuario)

    return render(
        request,
        'accounts/trocar_senha_inicial.html',
        {
            'form': form,
            'usuario': usuario,
            'modo_ativacao': False,
            'modo_recuperacao': True,
        },
    )


@login_required
def logout_view(request):
    if request.method == 'POST':
        logout(request)
        return redirect('login')
    return redirect('dashboard')


@login_required
def lista_usuarios(request):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    qs = Usuario.objects.select_related('gestor', 'aprovado_por').order_by('first_name', 'last_name', 'matricula')
    qs = _aplicar_filtros_usuarios(qs, q=q, status=status)

    params = request.GET.copy()
    params.pop('page', None)
    query_string = params.urlencode()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page'))
    stats = {
        'total': Usuario.objects.count(),
        'ativos': Usuario.objects.filter(ativo=True, solicitacao_pendente=False).count(),
        'pendentes': Usuario.objects.filter(solicitacao_pendente=True).count(),
        'inativos': Usuario.objects.filter(ativo=False, solicitacao_pendente=False).count(),
        'solicitantes': Usuario.objects.filter(nivel_acesso=NivelAcesso.VIEWER, solicitacao_pendente=False).count(),
        'tecnicos': Usuario.objects.filter(nivel_acesso=NivelAcesso.TECNICO, solicitacao_pendente=False).count(),
        'analistas': Usuario.objects.filter(nivel_acesso=NivelAcesso.ANALISTA, solicitacao_pendente=False).count(),
        'admins': Usuario.objects.filter(nivel_acesso=NivelAcesso.ADMIN, solicitacao_pendente=False).count(),
        'operacionais': Usuario.objects.filter(
            nivel_acesso__in=[NivelAcesso.TECNICO, NivelAcesso.ANALISTA, NivelAcesso.ADMIN],
            solicitacao_pendente=False,
        ).count(),
    }
    return render(
        request,
        'accounts/lista_usuarios.html',
        {'page_obj': page, 'q': q, 'status': status, 'stats': stats, 'query_string': query_string},
    )


@login_required
def usuarios_pendentes(request):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    q = request.GET.get('q', '').strip()
    qs = Usuario.objects.filter(solicitacao_pendente=True).select_related('gestor').order_by('created_at')
    qs = _aplicar_filtros_usuarios(qs, q=q, status='pendente')

    params = request.GET.copy()
    params.pop('page', None)
    query_string = params.urlencode()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page'))
    stats = {
        'pendentes': Usuario.objects.filter(solicitacao_pendente=True).count(),
        'com_gestor': Usuario.objects.filter(solicitacao_pendente=True, gestor__isnull=False).count(),
        'sem_gestor': Usuario.objects.filter(solicitacao_pendente=True, gestor__isnull=True).count(),
    }
    return render(
        request,
        'accounts/usuarios_pendentes.html',
        {'page_obj': page, 'q': q, 'stats': stats, 'query_string': query_string},
    )


@login_required
def aprovar_usuario(request, pk):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    usuario = get_object_or_404(Usuario, pk=pk, solicitacao_pendente=True)
    form = UsuarioApprovalForm(request.POST or None, instance=usuario)

    if form.is_valid():
        usuario = form.save(commit=False)
        usuario.aprovado_por = request.user
        usuario.aprovado_em = timezone.now()
        usuario.save()

        senha_temporaria = getattr(form, 'generated_password', '')
        eh_auto_gerada = getattr(form, 'generated_password_is_auto', False)
        link_primeiro_acesso = _activation_link(request, usuario)
        app_name = _site_name()

        email_enviado = False
        if usuario.email:
            assunto = f'Acesso aprovado no {app_name}'
            mensagem = f'''Olá {usuario.first_name or usuario.matricula},

Sua solicitação de acesso ao {app_name} foi aprovada.

Para criar sua senha de primeiro acesso, abra o link abaixo:
{link_primeiro_acesso}

Esse link é pessoal e deve ser usado apenas uma vez. Se ele expirar, procure o administrador para gerar um novo acesso.

Atenciosamente,
{app_name}
            '''

            try:
                send_mail(
                    assunto,
                    mensagem,
                    settings.DEFAULT_FROM_EMAIL,
                    [usuario.email],
                    fail_silently=False,
                )
                email_enviado = True
            except Exception:
                email_enviado = False

        notificar_usuario(
            usuario,
            'Acesso aprovado',
            (
                f'Seu acesso ao {app_name} foi liberado. '
                + (
                    'Verifique seu e-mail para criar a senha inicial.'
                    if email_enviado
                    else 'Procure o administrador para obter a senha temporária.'
                )
            ),
            link=reverse('login'),
        )
        notificar_admins(
            'Usuário aprovado',
            f'{usuario.nome_completo} ({usuario.matricula}) foi aprovado por {request.user.nome_completo}.',
            link=reverse('perfil_usuario', kwargs={'pk': usuario.pk}),
        )

        senha_info = 'gerada automaticamente' if eh_auto_gerada else 'definida pelo administrador'
        if email_enviado:
            mensagem_sucesso = (
                f'Conta de {usuario.nome_completo} aprovada. '
                f'Um link de primeiro acesso foi enviado para {usuario.email}.'
            )
        else:
            mensagem_sucesso = (
                f'Conta de {usuario.nome_completo} aprovada. '
                f'Senha temporária {senha_info}: {senha_temporaria}. '
                'Compartilhe a senha de forma segura com o usuário.'
            )

        messages.success(request, mensagem_sucesso)
        return redirect('usuarios_pendentes')

    return render(
        request,
        'accounts/aprovar_usuario.html',
        {'form': form, 'usuario': usuario},
    )


def ativar_conta(request, uidb64, token):
    usuario = _usuario_por_uidb64(uidb64)
    if not usuario or not account_activation_token.check_token(usuario, token):
        messages.error(
            request,
            'O link de primeiro acesso é inválido ou expirou. Solicite um novo link ao administrador.',
        )
        return redirect('login')

    if request.method == 'POST':
        form = TrocaSenhaInicialForm(usuario, request.POST)
        if form.is_valid():
            usuario = form.save()
            usuario.ativo = True
            usuario.solicitacao_pendente = False
            usuario.exigir_troca_senha = False
            if not usuario.aprovado_em:
                usuario.aprovado_em = timezone.now()
            usuario.save(update_fields=['ativo', 'solicitacao_pendente', 'exigir_troca_senha', 'aprovado_em', 'updated_at'])
            messages.success(request, 'Acesso ativado com sucesso. Agora você já pode entrar no sistema.')
            return redirect('login')
    else:
        form = TrocaSenhaInicialForm(usuario)

    return render(
        request,
        'accounts/trocar_senha_inicial.html',
        {
            'form': form,
            'usuario': usuario,
            'modo_ativacao': True,
            'modo_recuperacao': False,
        },
    )


@login_required
def trocar_senha_inicial(request):
    if not request.user.is_authenticated:
        return redirect('login')

    if not request.user.exigir_troca_senha:
        return redirect(_home_url_name(request.user))

    form = TrocaSenhaInicialForm(request.user, request.POST or None)
    if form.is_valid():
        usuario = form.save()
        usuario.exigir_troca_senha = False
        usuario.save(update_fields=['exigir_troca_senha', 'updated_at'])
        update_session_auth_hash(request, usuario)
        next_url = _safe_next_url(request, request.session.pop('post_password_change_next', None))
        messages.success(request, 'Senha alterada com sucesso. Seu acesso está liberado.')
        return redirect(next_url or _home_url_name(usuario))

    return render(
        request,
        'accounts/trocar_senha_inicial.html',
        {
            'form': form,
            'usuario': request.user,
            'modo_ativacao': False,
            'modo_recuperacao': False,
        },
    )


@login_required
def reprovar_usuario(request, pk):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    usuario = get_object_or_404(Usuario, pk=pk, solicitacao_pendente=True)
    if request.method != 'POST':
        return redirect('usuarios_pendentes')

    motivo = (request.POST.get('motivo_recusa') or '').strip() or 'Reprovado pelo administrador.'
    usuario.solicitacao_pendente = False
    usuario.ativo = False
    usuario.exigir_troca_senha = False
    usuario.motivo_recusa = motivo
    usuario.aprovado_em = None
    usuario.aprovado_por = None
    usuario.set_unusable_password()
    usuario.save()

    notificar_usuario(
        usuario,
        'Solicitação analisada',
        f'Sua solicitação de acesso ao {_site_name()} não foi aprovada. Motivo: {motivo}',
        link=reverse('login'),
    )
    messages.success(request, f'Solicitação de {usuario.nome_completo} foi reprovada.')
    return redirect('usuarios_pendentes')


@login_required
def criar_usuario(request):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    form = UsuarioCreateForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        user = form.save()
        notificar_usuario(
            user,
            'Conta criada',
            'Sua conta no ITAM System foi criada. Use a senha inicial e conclua o primeiro acesso.',
            link=reverse('login'),
        )
        messages.success(request, f'Usuário {user.matricula} criado com sucesso.')
        return redirect('lista_usuarios')
    return render(request, 'accounts/form_usuario.html', {'form': form, 'title': 'Novo usuário'})


@login_required
def editar_usuario(request, pk):
    if not request.user.is_admin:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    usuario = get_object_or_404(Usuario, pk=pk)
    antes = {
        'ativo': usuario.ativo,
        'nivel_acesso': usuario.nivel_acesso,
        'solicitacao_pendente': usuario.solicitacao_pendente,
        'exigir_troca_senha': usuario.exigir_troca_senha,
    }
    form = UsuarioUpdateForm(request.POST or None, request.FILES or None, instance=usuario)
    if form.is_valid():
        usuario = form.save()

        if (
            antes['ativo'] != usuario.ativo
            or antes['nivel_acesso'] != usuario.nivel_acesso
            or antes['solicitacao_pendente'] != usuario.solicitacao_pendente
            or antes['exigir_troca_senha'] != usuario.exigir_troca_senha
        ):
            notificar_usuario(
                usuario,
                'Dados de acesso atualizados',
                f'Seu perfil no ITAM System foi atualizado por {request.user.nome_completo}.',
                link=reverse('meu_perfil'),
            )

        messages.success(request, 'Usuário atualizado.')
        return redirect('lista_usuarios')

    return render(request, 'accounts/form_usuario.html', {'form': form, 'title': 'Editar usuário', 'obj': usuario})


@login_required
def perfil_usuario(request, pk=None):
    usuario = get_object_or_404(Usuario.objects.select_related('gestor', 'aprovado_por'), pk=pk) if pk else request.user

    if pk and not request.user.is_admin and usuario.pk != request.user.pk:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    equipamentos = Equipamento.objects.filter(responsavel=usuario, status='em_uso').order_by('id_patrimonio')
    historico = (
        Chamado.objects.filter(Q(solicitante=usuario) | Q(destinatario=usuario))
        .select_related('equipamento', 'responsavel', 'solicitante', 'destinatario')
        .order_by('-updated_at')[:10]
    )
    return render(
        request,
        'accounts/perfil.html',
        {
            'usuario': usuario,
            'equipamentos': equipamentos,
            'historico': historico,
        },
    )
