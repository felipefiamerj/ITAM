import re
import shutil
import tempfile

import pyotp
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import NivelAcesso, Usuario
from accounts.tokens import account_activation_token, password_recovery_token
from accounts.two_factor import (
    activate_two_factor,
    decrypt_secret,
    encrypt_secret,
    generate_recovery_codes,
    matching_totp_counter,
    recovery_code_hashes,
)

GIF_1X1 = (
    b'GIF89a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00\xff\xff\xff!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


@override_settings(ITAM_ADMIN_2FA_REQUIRED=True, ITAM_TWO_FACTOR_ENCRYPTION_KEY='test-two-factor-key')
class AdminTwoFactorTests(TestCase):
    password = 'SenhaAdminForte123!'

    def setUp(self):
        cache.clear()
        self.admin = Usuario.objects.create_superuser(
            matricula='2fa-admin',
            password=self.password,
            first_name='Admin',
            last_name='Seguro',
            email='admin@example.com',
        )
        self.viewer = Usuario.objects.create_user(
            matricula='2fa-viewer',
            password=self.password,
            first_name='Pessoa',
            last_name='Comum',
        )

    def _login_data(self, user):
        return {
            'form_type': 'login',
            'login-username': user.matricula,
            'login-password': self.password,
        }

    def _enable_admin_two_factor(self):
        secret = pyotp.random_base32()
        recovery_codes = generate_recovery_codes()
        self.admin.two_factor_enabled = True
        self.admin.two_factor_secret_encrypted = encrypt_secret(secret)
        self.admin.two_factor_recovery_hashes = recovery_code_hashes(recovery_codes)
        self.admin.two_factor_confirmed_at = timezone.now()
        self.admin.save(
            update_fields=[
                'two_factor_enabled',
                'two_factor_secret_encrypted',
                'two_factor_recovery_hashes',
                'two_factor_confirmed_at',
                'updated_at',
            ]
        )
        return secret, recovery_codes

    def test_primeiro_login_admin_exige_configuracao(self):
        response = self.client.post(reverse('login'), self._login_data(self.admin))

        self.assertRedirects(response, reverse('two_factor_setup'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.admin.pk)
        self.assertTrue(self.client.session['two_factor_setup_required'])

        blocked_response = self.client.get(reverse('dashboard'))
        self.assertRedirects(blocked_response, reverse('two_factor_setup'), fetch_redirect_response=False)

    def test_sessao_admin_anterior_a_atualizacao_tambem_exige_configuracao(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(response, reverse('two_factor_setup'), fetch_redirect_response=False)

    def test_sessao_admin_com_2fa_sem_desafio_e_encerrada(self):
        self._enable_admin_two_factor()
        self.client.force_login(self.admin)

        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("dashboard")}',
            fetch_redirect_response=False,
        )
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_configuracao_criptografa_segredo_e_gera_recuperacao(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session['itam_interactive_login'] = True
        session['two_factor_setup_required'] = True
        session.save()
        setup_response = self.client.get(reverse('two_factor_setup'))
        secret = self.client.session['two_factor_setup_secret']
        code = pyotp.TOTP(secret).now()

        response = self.client.post(reverse('two_factor_setup'), {'code': code})

        self.assertEqual(setup_response.status_code, 200)
        self.assertContains(setup_response, 'data:image/png;base64,')
        self.assertRedirects(response, reverse('two_factor_recovery_codes'), fetch_redirect_response=False)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)
        self.assertNotEqual(self.admin.two_factor_secret_encrypted, secret)
        self.assertEqual(decrypt_secret(self.admin.two_factor_secret_encrypted), secret)
        self.assertEqual(len(self.admin.two_factor_recovery_hashes), 8)
        self.assertEqual(len(self.client.session['two_factor_new_recovery_codes']), 8)

    def test_admin_com_2fa_so_recebe_sessao_depois_do_codigo(self):
        secret, _recovery_codes = self._enable_admin_two_factor()

        login_response = self.client.post(reverse('login'), self._login_data(self.admin))

        self.assertRedirects(login_response, reverse('two_factor_challenge'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

        challenge_response = self.client.post(
            reverse('two_factor_challenge'),
            {'code': pyotp.TOTP(secret).now()},
        )

        self.assertRedirects(challenge_response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.admin.pk)
        self.assertEqual(self.client.session['two_factor_verified_user_id'], self.admin.pk)

    def test_codigo_totp_nao_pode_ser_reutilizado(self):
        secret, _recovery_codes = self._enable_admin_two_factor()
        code = pyotp.TOTP(secret).now()
        self.client.post(reverse('login'), self._login_data(self.admin))
        first_response = self.client.post(reverse('two_factor_challenge'), {'code': code})
        self.client.logout()
        self.client.post(reverse('login'), self._login_data(self.admin))

        replay_response = self.client.post(reverse('two_factor_challenge'), {'code': code})

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(replay_response.status_code, 200)
        self.assertContains(replay_response, 'já utilizado')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_codigo_de_recuperacao_funciona_uma_vez(self):
        _secret, recovery_codes = self._enable_admin_two_factor()
        recovery_code = recovery_codes[0]
        self.client.post(reverse('login'), self._login_data(self.admin))

        response = self.client.post(reverse('two_factor_challenge'), {'code': recovery_code})

        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.admin.refresh_from_db()
        self.assertEqual(len(self.admin.two_factor_recovery_hashes), 7)

        self.client.logout()
        self.client.post(reverse('login'), self._login_data(self.admin))
        reused_response = self.client.post(reverse('two_factor_challenge'), {'code': recovery_code})
        self.assertEqual(reused_response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_usuario_comum_continua_com_login_direto(self):
        response = self.client.post(reverse('login'), self._login_data(self.viewer))

        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.viewer.pk)

    def test_ativacao_registra_contador_do_primeiro_codigo(self):
        secret = pyotp.random_base32()
        counter = matching_totp_counter(secret, pyotp.TOTP(secret).now())

        activate_two_factor(self.admin, secret, counter)

        self.admin.refresh_from_db()
        self.assertEqual(self.admin.two_factor_last_counter, counter)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ITAM_ADMIN_EMAILS=[],
    SITE_URL='https://itam.example.com',
)
class AccountActivationFlowTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            matricula='9000',
            password='test12345',
            first_name='Admin',
            last_name='ITAM',
        )
        self.pending = Usuario.objects.create_user(
            matricula='9001',
            password='senha-temporaria',
            first_name='Novo',
            last_name='Usuario',
            email='novo.usuario@example.com',
            ativo=False,
            solicitacao_pendente=True,
            exigir_troca_senha=False,
        )
        self.recovery_user = Usuario.objects.create_user(
            matricula='9002',
            password='senha-inicial',
            first_name='Recupera',
            last_name='Senha',
            email='recupera.senha@example.com',
            ativo=True,
            solicitacao_pendente=False,
            exigir_troca_senha=False,
        )

    def test_aprovacao_envia_link_de_primeiro_acesso(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('aprovar_usuario', args=[self.pending.pk]),
            {
                'nivel_acesso': NivelAcesso.TECNICO,
                'exigir_troca_senha': 'on',
            },
        )

        self.assertRedirects(response, reverse('usuarios_pendentes'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('accounts/ativar/', mail.outbox[0].body)
        self.assertIn('https://itam.example.com/accounts/ativar/', mail.outbox[0].body)

        self.pending.refresh_from_db()
        self.assertTrue(self.pending.ativo)
        self.assertFalse(self.pending.solicitacao_pendente)
        self.assertTrue(self.pending.exigir_troca_senha)

    def test_link_de_primeiro_acesso_permite_definir_nova_senha(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('aprovar_usuario', args=[self.pending.pk]),
            {
                'nivel_acesso': NivelAcesso.TECNICO,
                'exigir_troca_senha': 'on',
            },
        )

        self.pending.refresh_from_db()
        token = account_activation_token.make_token(self.pending)
        uidb64 = urlsafe_base64_encode(force_bytes(self.pending.pk))
        self.client.logout()

        response = self.client.post(
            reverse('ativar_conta', kwargs={'uidb64': uidb64, 'token': token}),
            {
                'new_password1': 'NovaSenhaForte123!',
                'new_password2': 'NovaSenhaForte123!',
            },
        )

        self.assertRedirects(response, reverse('login'))

        self.pending.refresh_from_db()
        self.assertTrue(self.pending.check_password('NovaSenhaForte123!'))
        self.assertFalse(self.pending.exigir_troca_senha)
        self.assertTrue(self.client.login(username=self.pending.matricula, password='NovaSenhaForte123!'))

    def test_solicitacao_recuperacao_envia_link_por_email(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.recovery_user.pk))
        response = self.client.post(
            reverse('recuperar_senha'),
            {'identificador': self.recovery_user.email},
        )

        self.assertRedirects(response, reverse('login'))
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(
            rf'https?://[^\s]+(?P<path>/accounts/recuperar-senha/{uidb64}/(?P<token>[^/]+)/)',
            mail.outbox[0].body,
        )
        self.assertIsNotNone(match)
        self.assertTrue(password_recovery_token.check_token(self.recovery_user, match.group('token')))
        self.assertIn(
            reverse('redefinir_senha', kwargs={'uidb64': uidb64, 'token': match.group('token')}),
            mail.outbox[0].body,
        )
        self.assertIn('recupera.senha@example.com', mail.outbox[0].to)

    @override_settings(ITAM_ADMIN_EMAILS=['felipefiamerj@gmail.com'])
    def test_solicitacao_recuperacao_notifica_email_administrativo(self):
        response = self.client.post(
            reverse('recuperar_senha'),
            {'identificador': self.recovery_user.email},
            HTTP_USER_AGENT='Navegador teste',
            REMOTE_ADDR='10.0.0.10',
        )

        self.assertRedirects(response, reverse('login'))
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('recupera.senha@example.com', mail.outbox[0].to)
        self.assertIn('felipefiamerj@gmail.com', mail.outbox[1].to)
        self.assertIn('Solicitacao de recuperacao de senha', mail.outbox[1].subject)
        self.assertIn(self.recovery_user.matricula, mail.outbox[1].body)
        self.assertIn('10.0.0.10', mail.outbox[1].body)
        self.assertNotIn('/accounts/recuperar-senha/', mail.outbox[1].body)

    def test_link_de_recuperacao_permite_redefinir_senha(self):
        token = password_recovery_token.make_token(self.recovery_user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.recovery_user.pk))

        response = self.client.post(
            reverse('redefinir_senha', kwargs={'uidb64': uidb64, 'token': token}),
            {
                'new_password1': 'NovaSenhaRecuperada123!',
                'new_password2': 'NovaSenhaRecuperada123!',
            },
        )

        self.assertRedirects(response, reverse('login'))
        self.recovery_user.refresh_from_db()
        self.assertTrue(self.recovery_user.check_password('NovaSenhaRecuperada123!'))
        self.assertTrue(self.client.login(username=self.recovery_user.matricula, password='NovaSenhaRecuperada123!'))


class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(ITAM_RATE_LIMIT_BYPASS_IPS=['179.218.106.52'])
    def test_login_nao_bloqueia_ip_configurado_como_permitido(self):
        response = None
        for _ in range(11):
            response = self.client.post(
                reverse('login'),
                {'username': 'naoexiste', 'password': 'senhaerrada'},
                REMOTE_ADDR='179.218.106.52',
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Identificador')

    @override_settings(ITAM_RATE_LIMIT_BYPASS_IPS=['179.218.106.52'])
    def test_login_nao_confia_em_forwarded_for_de_origem_direta(self):
        response = None
        for _ in range(11):
            response = self.client.post(
                reverse('login'),
                {'username': 'naoexiste', 'password': 'senhaerrada'},
                REMOTE_ADDR='198.51.100.20',
                HTTP_X_FORWARDED_FOR='179.218.106.52',
            )

        self.assertEqual(response.status_code, 429)

    @override_settings(
        ITAM_RATE_LIMIT_BYPASS_IPS=['179.218.106.52'],
        ITAM_TRUSTED_PROXY_IPS=['10.0.0.8'],
    )
    def test_login_reconhece_cliente_encaminhado_por_proxy_confiavel(self):
        response = None
        for _ in range(11):
            response = self.client.post(
                reverse('login'),
                {'username': 'naoexiste', 'password': 'senhaerrada'},
                REMOTE_ADDR='10.0.0.8',
                HTTP_X_FORWARDED_FOR='179.218.106.52',
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Identificador')

    @override_settings(
        ITAM_RATE_LIMIT_BYPASS_IPS=['179.218.106.52'],
        ITAM_TRUSTED_PROXY_IPS=['10.0.0.8'],
    )
    def test_login_usa_ip_nao_confiavel_mais_proximo_na_cadeia(self):
        response = None
        for _ in range(11):
            response = self.client.post(
                reverse('login'),
                {'username': 'naoexiste', 'password': 'senhaerrada'},
                REMOTE_ADDR='10.0.0.8',
                HTTP_X_FORWARDED_FOR='179.218.106.52, 198.51.100.20',
            )

        self.assertEqual(response.status_code, 429)

    @override_settings(ITAM_RATE_LIMIT_BYPASS_IPS=['179.218.106.52'])
    def test_login_de_ip_permitido_nao_libera_usuario_pendente(self):
        usuario = Usuario.objects.create_user(
            matricula='9003',
            password='senha-forte-123',
            first_name='Usuário',
            last_name='Pendente',
            ativo=False,
            solicitacao_pendente=True,
            exigir_troca_senha=False,
        )

        response = self.client.post(
            reverse('login'),
            {'username': usuario.matricula, 'password': 'senha-forte-123'},
            REMOTE_ADDR='179.218.106.52',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.session.get('_auth_user_id'))

        usuario.refresh_from_db()
        self.assertFalse(usuario.ativo)
        self.assertTrue(usuario.solicitacao_pendente)
        self.assertFalse(usuario.exigir_troca_senha)

    @override_settings(ITAM_RATE_LIMIT_BYPASS_IPS=['179.218.106.52'])
    def test_login_de_ip_permitido_nao_reativa_usuario_inativo(self):
        usuario = Usuario.objects.create_user(
            matricula='9004',
            password='senha-forte-123',
            first_name='Usuario',
            last_name='Inativo',
            ativo=False,
            solicitacao_pendente=False,
            exigir_troca_senha=True,
        )

        response = self.client.post(
            reverse('login'),
            {'username': usuario.matricula, 'password': 'senha-forte-123'},
            REMOTE_ADDR='179.218.106.52',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.session.get('_auth_user_id'))

        usuario.refresh_from_db()
        self.assertFalse(usuario.ativo)
        self.assertFalse(usuario.solicitacao_pendente)
        self.assertTrue(usuario.exigir_troca_senha)


class UserPhotoDisplayTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()

        self.admin = Usuario.objects.create_superuser(
            matricula='9100',
            password='test12345',
            first_name='Admin',
            last_name='Foto',
            foto=SimpleUploadedFile('admin-foto.gif', GIF_1X1, content_type='image/gif'),
        )
        self.colaborador = Usuario.objects.create_user(
            matricula='9101',
            password='test12345',
            first_name='Foto',
            last_name='Lista',
            foto=SimpleUploadedFile('lista-foto.gif', GIF_1X1, content_type='image/gif'),
        )

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def test_perfil_mostra_foto_do_usuario(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('meu_perfil'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.admin.foto.url)
        self.assertContains(response, 'user-avatar--lg')

    def test_lista_de_usuarios_mostra_foto_na_tabela(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('lista_usuarios'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.colaborador.foto.url)
        self.assertContains(response, 'user-avatar--xs')
