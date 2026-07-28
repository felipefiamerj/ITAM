"""ITAM System - IT Asset Management."""

import os
from pathlib import Path

from celery.schedules import crontab

def _read_dotenv(path):
    values = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:]
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


DOTENV_VALUES = _read_dotenv(Path(__file__).resolve().parent.parent / '.env')


def config(name, default=None, cast=None, prefer_env=True):
    if prefer_env:
        value = os.environ.get(name, DOTENV_VALUES.get(name, default))
    else:
        value = DOTENV_VALUES.get(name, os.environ.get(name, default))
    if cast is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}
    if cast is int and value is not None:
        return int(value)
    if cast is float and value is not None:
        return float(value)
    if cast is not None and callable(cast):
        return cast(value)
    return value

BASE_DIR = Path(__file__).resolve().parent.parent

DJANGO_ENV = config('DJANGO_ENV', default='development').strip().lower()
APP_NAME = config('APP_NAME', default='ITAM System')
APP_SHORT_NAME = config('APP_SHORT_NAME', default='ITAM')

SECRET_KEY = config('SECRET_KEY', default='', prefer_env=False)
if not SECRET_KEY:
    if DJANGO_ENV == 'production':
        raise RuntimeError('SECRET_KEY precisa ser definido no ambiente de producao.')
    SECRET_KEY = 'django-insecure-change-this-in-production-itam-2026'

DEBUG = config('DEBUG', default=DJANGO_ENV in {'development', 'dev', 'local'}, cast=bool, prefer_env=False)
ALLOWED_HOSTS = [
    host.strip()
    for host in config('ALLOWED_HOSTS', default='127.0.0.1,localhost,testserver').split(',')
    if host.strip()
]
SITE_URL = config('SITE_URL', default='')
CSRF_TRUSTED_ORIGINS = [SITE_URL] if SITE_URL.startswith(('http://', 'https://')) else []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'crispy_forms',
    'crispy_bootstrap5',
    'widget_tweaks',
    'django_filters',
    'auditlog',
    'guardian',
    'import_export',
    'django_celery_beat',
    'django_celery_results',
    'channels',
    # Local apps
    'accounts.apps.AccountsConfig',
    'equipamentos.apps.EquipamentosConfig',
    'chamados.apps.ChamadosConfig',
    'estoque.apps.EstoqueConfig',
    'dashboard.apps.DashboardConfig',
    'ia.apps.IaConfig',
    'notifications.apps.NotificationsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.ForcePasswordChangeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'auditlog.middleware.AuditlogMiddleware',
]

ROOT_URLCONF = 'itam.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'itam.context_processors.site_context',
                'accounts.context_processors.accounts_context',
                'notifications.context_processors.notifications_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'itam.wsgi.application'
ASGI_APPLICATION = 'itam.asgi.application'

DB_ENGINE = config('DB_ENGINE', default='django.db.backends.sqlite3')
if DB_ENGINE == 'django.db.backends.sqlite3':
    DATABASES = {
        'default': {
            'ENGINE': DB_ENGINE,
            'NAME': BASE_DIR / config('DB_NAME', default='db.sqlite3'),
        }
    }
elif DB_ENGINE == 'django.db.backends.postgresql':
    DATABASES = {
        'default': {
            'ENGINE': DB_ENGINE,
            'NAME': config('DB_NAME', default='Itam_DB'),
            'USER': config('DB_USER', default='postgres'),
            'PASSWORD': config('DB_PASSWORD', default=''),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': DB_ENGINE,
            'NAME': config('DB_NAME', default='itam_db'),
            'USER': config('DB_USER', default='itam'),
            'PASSWORD': config('DB_PASSWORD', default='itam123'),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='3306'),
            'OPTIONS': {
                'charset': 'utf8mb4',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTH_USER_MODEL = 'accounts.Usuario'
AUTHENTICATION_BACKENDS = [
    'accounts.backends.MatriculaBackend',
    'django.contrib.auth.backends.ModelBackend',
    'guardian.backends.ObjectPermissionBackend',
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
if DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
ITAM_ESTOQUE_ALERTA_MINIMO = config('ITAM_ESTOQUE_ALERTA_MINIMO', default=20, cast=int)

CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

REDIS_URL = config('REDIS_URL', default='')
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=REDIS_URL or '')
if not CELERY_BROKER_URL:
    if DJANGO_ENV == 'production':
        raise RuntimeError('Defina CELERY_BROKER_URL ou REDIS_URL em producao.')
    CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'django-db'
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_BEAT_SCHEDULE = {
    'itam-recalcular-scores-diario': {
        'task': 'equipamentos.recalcular_scores',
        'schedule': crontab(hour=2, minute=0),
    },
    'itam-verificar-monitoramento-frequente': {
        'task': 'equipamentos.verificar_monitoramento',
        'schedule': crontab(minute='*/5'),
    },
    'itam-verificar-sla-chamados': {
        'task': 'chamados.verificar_sla_chamados',
        'schedule': crontab(minute='*/5'),
    },
}

if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {'hosts': [REDIS_URL]},
        }
    }
elif DJANGO_ENV == 'production':
    raise RuntimeError('Defina REDIS_URL em producao para habilitar Channels com Redis.')
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        }
    }

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=False, cast=bool)
    SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=0, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = config('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False, cast=bool)
    SECURE_HSTS_PRELOAD = config('SECURE_HSTS_PRELOAD', default=False, cast=bool)

EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=f'{APP_NAME} <noreply@empresa.com>')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': ['django_filters.rest_framework.DjangoFilterBackend'],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

AUDITLOG_INCLUDE_ALL_MODELS = False

ITAM_ESTOQUE_ALERTA_MINIMO = config('ITAM_ESTOQUE_ALERTA_MINIMO', default=5, cast=int)
ITAM_PREVISAO_DIAS = config('ITAM_PREVISAO_DIAS', default=30, cast=int)
ITAM_HEARTBEAT_STALE_MINUTES = config('ITAM_HEARTBEAT_STALE_MINUTES', default=10, cast=int)
ITAM_MONITORING_ALERT_COOLDOWN_MINUTES = config('ITAM_MONITORING_ALERT_COOLDOWN_MINUTES', default=30, cast=int)
ITAM_API_SHARED_KEY = config('ITAM_API_SHARED_KEY', default='')
ITAM_API_SERVICE_MATRICULA = config('ITAM_API_SERVICE_MATRICULA', default='')
