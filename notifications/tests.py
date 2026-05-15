from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Usuario

from .models import Notification
from .services import notificar_usuarios


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
