from smtplib import SMTPException

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError

from notifications.integrations import configured_webhooks, send_corporate_notification


class Command(BaseCommand):
    help = 'Testa integracoes corporativas de email SMTP e webhooks Teams/Slack.'

    def add_arguments(self, parser):
        parser.add_argument('--email-to', default='', help='Destinatario para envio de email de teste.')
        parser.add_argument('--webhooks', action='store_true', help='Envia notificacao de teste para webhooks configurados.')
        parser.add_argument('--all', action='store_true', help='Executa todos os testes possiveis.')

    def handle(self, *args, **options):
        email_to = (options['email_to'] or '').strip()
        test_webhooks = options['webhooks'] or options['all']
        test_email = bool(email_to)

        if options['all'] and not email_to:
            raise CommandError('Use --email-to junto com --all para testar SMTP.')

        if not test_email and not test_webhooks:
            self._print_status()
            self.stdout.write('')
            self.stdout.write('Use --email-to suporte@empresa.com, --webhooks ou --all.')
            return

        if test_email:
            self._test_email(email_to)

        if test_webhooks:
            self._test_webhooks()

    def _print_status(self):
        self.stdout.write(self.style.MIGRATE_HEADING('Integracoes corporativas'))
        self.stdout.write(f'EMAIL_BACKEND: {getattr(settings, "EMAIL_BACKEND", "")}')
        self.stdout.write(f'EMAIL_HOST: {getattr(settings, "EMAIL_HOST", "") or "-"}')
        self.stdout.write(f'DEFAULT_FROM_EMAIL: {getattr(settings, "DEFAULT_FROM_EMAIL", "") or "-"}')
        self.stdout.write(
            f'ITAM_CORPORATE_WEBHOOKS_ENABLED: {getattr(settings, "ITAM_CORPORATE_WEBHOOKS_ENABLED", False)}'
        )
        webhooks = configured_webhooks()
        self.stdout.write(f'Webhooks configurados: {len(webhooks)}')
        for provider, _url in webhooks:
            self.stdout.write(f'  - {provider}')

    def _test_email(self, email_to):
        try:
            sent = send_mail(
                subject=f'Teste de email - {settings.APP_NAME}',
                message='Este e um email de teste das integracoes corporativas do FIAME System.',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email_to],
                fail_silently=False,
            )
        except SMTPException as exc:
            raise CommandError(f'Falha SMTP: {exc}') from exc
        except OSError as exc:
            raise CommandError(f'Falha de conexao SMTP: {exc}') from exc
        if sent != 1:
            raise CommandError('Email de teste nao foi confirmado pelo backend SMTP.')
        self.stdout.write(self.style.SUCCESS(f'Email de teste enviado para {email_to}.'))

    def _test_webhooks(self):
        if not getattr(settings, 'ITAM_CORPORATE_WEBHOOKS_ENABLED', False):
            raise CommandError('ITAM_CORPORATE_WEBHOOKS_ENABLED precisa estar True para testar webhooks.')
        if not configured_webhooks():
            raise CommandError('Nenhum webhook corporativo configurado.')

        results = send_corporate_notification(
            'Teste de integracao corporativa',
            mensagem='Mensagem de teste enviada pelo comando testar_integracoes.',
            link='/dashboard/',
            audience='teste',
        )
        failed = [result for result in results if not result['ok']]
        for result in results:
            status = result['status_code'] or '-'
            label = 'OK' if result['ok'] else 'ERRO'
            self.stdout.write(f'{label}: {result["provider"]} status={status}')
        if failed:
            raise CommandError('Um ou mais webhooks falharam.')
        self.stdout.write(self.style.SUCCESS('Webhooks corporativos testados com sucesso.'))
