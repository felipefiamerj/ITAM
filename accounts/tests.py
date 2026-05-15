from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import NivelAcesso, Usuario
from accounts.tokens import account_activation_token


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
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
        self.assertNotIn('127.0.0.1', mail.outbox[0].body)

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
