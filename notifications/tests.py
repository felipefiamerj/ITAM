from django.test import TestCase

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
        notificar_usuarios([self.usuario], 'Aviso', 'Mensagem de teste', '/dashboard/')

        notification = Notification.objects.get()

        self.assertEqual(notification.user, self.usuario)
        self.assertEqual(notification.title, 'Aviso')
        self.assertIsNotNone(notification.created_at)
