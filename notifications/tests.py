from unittest.mock import AsyncMock, MagicMock, patch

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import NivelAcesso, Usuario

from .models import Notification
from .routing import websocket_urlpatterns
from .services import notificar_time_operacional, notificar_usuarios


class NotificationRealtimeSettingsTests(SimpleTestCase):
    def test_daphne_habilita_runserver_asgi_para_websocket(self):
        self.assertIn('daphne', settings.INSTALLED_APPS)
        self.assertLess(
            settings.INSTALLED_APPS.index('daphne'),
            settings.INSTALLED_APPS.index('django.contrib.staticfiles'),
        )
        self.assertEqual(settings.ASGI_APPLICATION, 'itam.asgi.application')

    async def test_rota_websocket_notificacoes_chega_no_consumer(self):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            '/ws/notifications/',
        )

        connected, close_code = await communicator.connect()

        self.assertFalse(connected)
        self.assertEqual(close_code, 4401)
        await communicator.disconnect()


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            matricula='2001',
            password='senha-forte-123',
            first_name='Bruno',
            last_name='Lima',
        )

    def test_notificar_usuarios_em_lote_define_created_at(self):
        with self.captureOnCommitCallbacks(execute=True):
            notificar_usuarios([self.usuario], 'Aviso', 'Mensagem de teste', '/dashboard/')

        notification = Notification.objects.get()

        self.assertEqual(notification.user, self.usuario)
        self.assertEqual(notification.title, 'Aviso')
        self.assertIsNotNone(notification.created_at)

    @patch('notifications.realtime.get_channel_layer')
    def test_notificar_usuarios_dispara_broadcast_realtime(self, mock_get_channel_layer):
        layer = MagicMock()
        layer.group_send = AsyncMock()
        mock_get_channel_layer.return_value = layer

        with self.captureOnCommitCallbacks(execute=True):
            notificar_usuarios([self.usuario], 'Aviso', 'Mensagem de teste', '/dashboard/')

        layer.group_send.assert_awaited_once()
        group_name, payload = layer.group_send.await_args.args
        self.assertEqual(group_name, f'notifications.user.{self.usuario.pk}')
        self.assertEqual(payload['type'], 'notifications.message')
        self.assertEqual(payload['payload']['event'], 'notification.created')
        self.assertEqual(payload['payload']['notification']['title'], 'Aviso')

    @patch('notifications.views.broadcast_user_state')
    def test_read_all_dispara_atualizacao_em_tempo_real(self, mock_broadcast):
        Notification.objects.create(
            user=self.usuario,
            title='Aviso',
            message='Mensagem de teste',
            link='/dashboard/',
        )

        self.client.force_login(self.usuario)
        self.client.get(reverse('notification_read_all'))

        mock_broadcast.assert_called_once_with(self.usuario)

    @override_settings(
        ITAM_CORPORATE_WEBHOOKS_ENABLED=True,
        ITAM_TEAMS_WEBHOOK_URL='https://teams.example/webhook',
        SITE_URL='https://itam.example.com',
    )
    @patch('notifications.integrations.requests.post')
    def test_notificar_time_operacional_dispara_webhook_corporativo(self, mock_post):
        self.usuario.nivel_acesso = NivelAcesso.TECNICO
        self.usuario.save(update_fields=['nivel_acesso'])
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        mock_post.return_value = response

        with self.captureOnCommitCallbacks(execute=True):
            notificar_time_operacional('Alerta', 'Mensagem operacional', '/dashboard/')

        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(url, 'https://teams.example/webhook')
        self.assertIn('FIAME System: Alerta', payload['title'])
        self.assertIn('https://itam.example.com/dashboard/', payload['text'])

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        ITAM_CORPORATE_WEBHOOKS_ENABLED=False,
    )
    def test_testar_integracoes_envia_email_de_teste(self):
        call_command('testar_integracoes', '--email-to', 'suporte@example.com')

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Teste de email', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ['suporte@example.com'])
