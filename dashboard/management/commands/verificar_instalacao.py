import redis
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

PRODUCTION_ENV = 'production'
WEAK_SECRET_PREFIXES = ('django-insecure-', 'change-me', 'release-secret')
REQUIRED_STATIC_ASSETS = (
    'css/app.css',
    'img/fiame-mark.svg',
    'img/fiame-login-hero-panel.png',
    'vendor/bootstrap/5.3.3/css/bootstrap.min.css',
    'vendor/fontawesome/6.5.2/css/all.min.css',
    'vendor/google-fonts/inter-sora/inter-sora.css',
)


class Command(BaseCommand):
    help = 'Verifica se o ambiente esta pronto para instalar e operar o sistema.'

    def handle(self, *args, **options):
        erros = []
        avisos = []

        self.stdout.write(self.style.MIGRATE_HEADING('Verificacao de ambiente'))
        self.stdout.write(f'APP_NAME: {getattr(settings, "APP_NAME", "FIAME System")}')
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

        is_production = getattr(settings, 'DJANGO_ENV', 'development') == PRODUCTION_ENV

        if settings.DEBUG and is_production:
            erro('DEBUG nao pode ficar ativo em producao.')

        if is_production:
            secret_key = getattr(settings, 'SECRET_KEY', '')
            if _secret_key_fraca(secret_key):
                erro('SECRET_KEY precisa ser longa, aleatoria e sem prefixos de desenvolvimento.')
            if 'sqlite3' in settings.DATABASES['default']['ENGINE']:
                erro('SQLite nao deve ser usado em producao. Configure PostgreSQL ou MySQL.')
            if not getattr(settings, 'REDIS_URL', ''):
                erro('REDIS_URL e obrigatorio em producao para Channels e automacoes.')
            if not settings.ALLOWED_HOSTS or settings.ALLOWED_HOSTS == ['*']:
                erro('ALLOWED_HOSTS precisa ser explicitado em producao.')
            if not getattr(settings, 'SITE_URL', ''):
                aviso('SITE_URL nao foi definido. Links absolutos podem depender da requisicao.')
            elif not settings.SITE_URL.startswith('https://'):
                erro('SITE_URL precisa usar HTTPS em producao.')
            if not getattr(settings, 'SECURE_SSL_REDIRECT', False):
                erro('SECURE_SSL_REDIRECT precisa ficar True em producao.')
            if getattr(settings, 'SECURE_HSTS_SECONDS', 0) < 31536000:
                erro('SECURE_HSTS_SECONDS precisa ter pelo menos 31536000 em producao.')
            if not getattr(settings, 'SESSION_COOKIE_SECURE', False):
                erro('SESSION_COOKIE_SECURE precisa ficar True em producao.')
            if not getattr(settings, 'CSRF_COOKIE_SECURE', False):
                erro('CSRF_COOKIE_SECURE precisa ficar True em producao.')
            if not getattr(settings, 'ITAM_ADMIN_2FA_REQUIRED', False):
                erro('ITAM_ADMIN_2FA_REQUIRED precisa ficar True em producao.')
            if not getattr(settings, 'ITAM_TWO_FACTOR_ENCRYPTION_KEY', ''):
                aviso('ITAM_TWO_FACTOR_ENCRYPTION_KEY nao foi definida; os segredos 2FA usarao a SECRET_KEY.')
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

        for asset_path in REQUIRED_STATIC_ASSETS:
            if finders.find(asset_path):
                ok(f'Asset estatico encontrado: {asset_path}')
            else:
                erro(f'Asset estatico ausente: {asset_path}')

        email_backend = getattr(settings, 'EMAIL_BACKEND', '')
        if email_backend == 'django.core.mail.backends.console.EmailBackend':
            mensagem = 'EMAIL_BACKEND esta em console. Isso e util para desenvolvimento, mas nao envia email real.'
            if is_production:
                erro(mensagem)
            else:
                aviso(mensagem)
        else:
            host = getattr(settings, 'EMAIL_HOST', '')
            user = getattr(settings, 'EMAIL_HOST_USER', '')
            password = getattr(settings, 'EMAIL_HOST_PASSWORD', '')
            if host and user and password:
                ok('Configuracao de email presente.')
            elif is_production:
                erro('Email precisa estar completo em producao para primeiro acesso e recuperacao de senha.')
            else:
                aviso('Email configurado parcialmente. Verifique host, usuario e senha antes de ir para producao.')

        webhooks_enabled = getattr(settings, 'ITAM_CORPORATE_WEBHOOKS_ENABLED', False)
        webhooks = [
            value
            for value in (
                getattr(settings, 'ITAM_TEAMS_WEBHOOK_URL', ''),
                getattr(settings, 'ITAM_SLACK_WEBHOOK_URL', ''),
            )
            if value
        ]
        if webhooks_enabled and webhooks:
            ok(f'Webhooks corporativos configurados: {len(webhooks)}.')
        elif webhooks_enabled and not webhooks:
            mensagem = 'ITAM_CORPORATE_WEBHOOKS_ENABLED esta True, mas nenhum webhook foi configurado.'
            if is_production:
                erro(mensagem)
            else:
                aviso(mensagem)
        elif webhooks:
            aviso('Webhooks corporativos foram informados, mas ITAM_CORPORATE_WEBHOOKS_ENABLED esta False.')

        if erros:
            self.stdout.write(self.style.ERROR('Verificacao concluida com erros.'))
            raise CommandError('\n'.join(erros))

        if avisos:
            self.stdout.write(self.style.WARNING(f'Verificacao concluida com {len(avisos)} aviso(s).'))
        else:
            self.stdout.write(self.style.SUCCESS('Ambiente pronto para instalacao.'))


def _secret_key_fraca(secret_key):
    if len(secret_key) < 50:
        return True
    lowered = secret_key.lower()
    return any(lowered.startswith(prefix) for prefix in WEAK_SECRET_PREFIXES)
