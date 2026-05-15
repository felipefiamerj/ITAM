from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

import redis


class Command(BaseCommand):
    help = 'Verifica se o ambiente esta pronto para instalar e operar o sistema.'

    def handle(self, *args, **options):
        erros = []
        avisos = []

        self.stdout.write(self.style.MIGRATE_HEADING('Verificacao de ambiente'))
        self.stdout.write(f'APP_NAME: {getattr(settings, "APP_NAME", "ITAM System")}')
        self.stdout.write(f'DJANGO_ENV: {getattr(settings, "DJANGO_ENV", "development")}')
        self.stdout.write(f'DATABASE_ENGINE: {settings.DATABASES["default"]["ENGINE"]}')

        def erro(mensagem):
            erros.append(mensagem)
            self.stdout.write(self.style.ERROR(f'ERRO: {mensagem}'))

        def aviso(mensagem):
            avisos.append(mensagem)
            self.stdout.write(self.style.WARNING(f'AVISO: {mensagem}'))

        def ok(mensagem):
            self.stdout.write(self.style.SUCCESS(f'OK: {mensagem}'))

        if settings.DEBUG and getattr(settings, 'DJANGO_ENV', 'development') == 'production':
            erro('DEBUG nao pode ficar ativo em producao.')

        if getattr(settings, 'DJANGO_ENV', 'development') == 'production':
            if not getattr(settings, 'REDIS_URL', ''):
                erro('REDIS_URL e obrigatorio em producao para Channels e automacoes.')
            if not settings.ALLOWED_HOSTS or settings.ALLOWED_HOSTS == ['*']:
                erro('ALLOWED_HOSTS precisa ser explicitado em producao.')
            if not getattr(settings, 'SITE_URL', ''):
                aviso('SITE_URL nao foi definido. Links absolutos podem depender da requisicao.')
        else:
            if not getattr(settings, 'REDIS_URL', ''):
                aviso('REDIS_URL nao configurado. Celery/Channels vao usar fallback de desenvolvimento.')

        try:
            conexao = connections['default']
            conexao.ensure_connection()
            with conexao.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            ok('Banco de dados conectado.')
        except Exception as exc:
            erro(f'Banco de dados indisponivel: {exc}')

        redis_url = getattr(settings, 'REDIS_URL', '')
        if redis_url:
            try:
                cliente = redis.from_url(redis_url)
                cliente.ping()
                ok('Redis respondendo.')
            except Exception as exc:
                erro(f'Redis indisponivel: {exc}')

        email_backend = getattr(settings, 'EMAIL_BACKEND', '')
        if email_backend == 'django.core.mail.backends.console.EmailBackend':
            aviso('EMAIL_BACKEND esta em console. Isso e util para desenvolvimento, mas nao envia email real.')
        else:
            host = getattr(settings, 'EMAIL_HOST', '')
            user = getattr(settings, 'EMAIL_HOST_USER', '')
            password = getattr(settings, 'EMAIL_HOST_PASSWORD', '')
            if host and user and password:
                ok('Configuracao de email presente.')
            else:
                aviso('Email configurado parcialmente. Verifique host, usuario e senha antes de ir para producao.')

        if erros:
            self.stdout.write(self.style.ERROR('Verificacao concluida com erros.'))
            raise CommandError('\n'.join(erros))

        if avisos:
            self.stdout.write(self.style.WARNING(f'Verificacao concluida com {len(avisos)} aviso(s).'))
        else:
            self.stdout.write(self.style.SUCCESS('Ambiente pronto para instalacao.'))
