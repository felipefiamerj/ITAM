"""FIAME System - IT Asset Management."""

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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


def _parse_bool_value(value):
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    return None


def config(name, default=None, cast=None, prefer_env=True):
    if prefer_env:
        value = os.environ.get(name, DOTENV_VALUES.get(name, default))
    else:
        value = DOTENV_VALUES.get(name, os.environ.get(name, default))
    if cast is bool:
        parsed = _parse_bool_value(value)
        if parsed is not None:
            return parsed
        fallback = DOTENV_VALUES.get(name, default) if prefer_env else os.environ.get(name, default)
        parsed = _parse_bool_value(fallback)
        if parsed is not None:
            return parsed
        return bool(default)
    if cast is int and value is not None:
        return int(value)
    if cast is float and value is not None:
        return float(value)
    if cast is not None and callable(cast):
        return cast(value)
    return value


def csv_config(name, default=''):
    return [
        item.strip()
        for item in str(config(name, default=default) or '').split(',')
        if item.strip()
    ]


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / config('LOG_DIR', default='logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = str(config('LOG_LEVEL', default='INFO')).upper()
LOG_FILE_MAX_BYTES = config('LOG_FILE_MAX_BYTES', default=10 * 1024 * 1024, cast=int)
LOG_FILE_BACKUP_COUNT = config('LOG_FILE_BACKUP_COUNT', default=10, cast=int)


def _database_config_from_url(database_url):
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()
    scheme_to_engine = {
        'postgres': 'django.db.backends.postgresql',
        'postgresql': 'django.db.backends.postgresql',
        'mysql': 'django.db.backends.mysql',
        'sqlite': 'django.db.backends.sqlite3',
    }
    if scheme not in scheme_to_engine:
        raise RuntimeError(f'DATABASE_URL usa um esquema nao suportado: {scheme}')

    engine = scheme_to_engine[scheme]
    if engine == 'django.db.backends.sqlite3':
        db_path = unquote(parsed.path.lstrip('/')) or unquote(parsed.netloc) or ':memory:'
        name = db_path if db_path == ':memory:' or Path(db_path).is_absolute() else BASE_DIR / db_path
        return {
            'ENGINE': engine,
            'NAME': name,
        }

    database = {
        'ENGINE': engine,
        'NAME': unquote(parsed.path.lstrip('/')),
        'USER': unquote(parsed.username or ''),
        'PASSWORD': unquote(parsed.password or ''),
        'HOST': parsed.hostname or '',
        'PORT': str(parsed.port or ''),
    }
    if engine == 'django.db.backends.mysql':
        database['OPTIONS'] = {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        }

    query = parse_qs(parsed.query)
    if 'sslmode' in query and engine == 'django.db.backends.postgresql':
        database.setdefault('OPTIONS', {})['sslmode'] = query['sslmode'][0]

    return database

DJANGO_ENV = config('DJANGO_ENV', default='development').strip().lower()
APP_NAME = config('APP_NAME', default='FIAME System')
APP_SHORT_NAME = config('APP_SHORT_NAME', default='FIAME')
APP_STATIC_VERSION = config('APP_STATIC_VERSION', default='20260817-2fa')

SECRET_KEY = config('SECRET_KEY', default='')
if not SECRET_KEY:
    if DJANGO_ENV == 'production':
        raise RuntimeError('SECRET_KEY precisa ser definido no ambiente de producao.')
    SECRET_KEY = 'django-insecure-change-this-in-production-itam-2026'

DEBUG = config('DEBUG', default=DJANGO_ENV in {'development', 'dev', 'local'}, cast=bool)
ALLOWED_HOSTS = [
    host.strip()
    for host in config('ALLOWED_HOSTS', default='127.0.0.1,localhost,testserver').split(',')
    if host.strip()
]
SITE_URL = config('SITE_URL', default='')
ITAM_QR_BASE_URL = config('ITAM_QR_BASE_URL', default=SITE_URL)
CSRF_TRUSTED_ORIGINS = csv_config('CSRF_TRUSTED_ORIGINS') or (
    [SITE_URL] if SITE_URL.startswith(('http://', 'https://')) else []
)
CORS_ALLOWED_ORIGINS = csv_config('CORS_ALLOWED_ORIGINS')
CORS_ALLOW_CREDENTIALS = config('CORS_ALLOW_CREDENTIALS', default=False, cast=bool)

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'corsheaders',
    'rest_framework',
    'drf_spectacular',
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
    'corsheaders.middleware.CorsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'itam.middleware.RateLimitResponseMiddleware',
    'accounts.middleware.ForcePasswordChangeMiddleware',
    'accounts.middleware.ForceAdminTwoFactorSetupMiddleware',
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

DATABASE_URL = config('DATABASE_URL', default='')
DB_ENGINE = config('DB_ENGINE', default='django.db.backends.sqlite3')
if DATABASE_URL:
    DATABASES = {
        'default': _database_config_from_url(DATABASE_URL),
    }
elif DB_ENGINE == 'django.db.backends.sqlite3':
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
            'PASSWORD': config('DB_PASSWORD', default=''),
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
BACKUP_DIR = Path(config('BACKUP_DIR', default=str(BASE_DIR / 'backups')))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

REDIS_URL = config('REDIS_URL', default='')
CACHE_URL = config('CACHE_URL', default=REDIS_URL or '')
if CACHE_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': CACHE_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            },
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'itam-system-cache',
        }
    }

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
ITAM_TERMO_ASSINATURA_COBRANCA_AUTOMATICA = config('ITAM_TERMO_ASSINATURA_COBRANCA_AUTOMATICA', default=True, cast=bool)
ITAM_TERMO_ASSINATURA_COBRANCA_INTERVALO_DIAS = config('ITAM_TERMO_ASSINATURA_COBRANCA_INTERVALO_DIAS', default=1, cast=int)
ITAM_TERMO_ASSINATURA_COBRANCA_HORA = min(23, max(0, config('ITAM_TERMO_ASSINATURA_COBRANCA_HORA', default=8, cast=int)))
ITAM_TERMO_ASSINATURA_COBRANCA_MINUTO = min(59, max(0, config('ITAM_TERMO_ASSINATURA_COBRANCA_MINUTO', default=0, cast=int)))
CELERY_BEAT_SCHEDULE = {
    'itam-recalcular-scores-diario': {
        'task': 'equipamentos.recalcular_scores',
        'schedule': crontab(hour=2, minute=0),
    },
    'itam-verificar-monitoramento-frequente': {
        'task': 'equipamentos.verificar_monitoramento',
        'schedule': crontab(minute='*/5'),
    },
    'itam-verificar-ciclo-vida-diario': {
        'task': 'equipamentos.verificar_ciclo_vida',
        'schedule': crontab(hour=7, minute=30),
    },
    'itam-verificar-sla-chamados': {
        'task': 'chamados.verificar_sla_chamados',
        'schedule': crontab(minute='*/5'),
    },
    'itam-verificar-sla-etapas-chamados': {
        'task': 'chamados.verificar_sla_etapas_chamados',
        'schedule': crontab(minute='*/5'),
    },
    'itam-verificar-saude-sistema': {
        'task': 'dashboard.verificar_saude_sistema',
        'schedule': crontab(minute='*/5'),
    },
}
if ITAM_TERMO_ASSINATURA_COBRANCA_AUTOMATICA:
    CELERY_BEAT_SCHEDULE['itam-cobrar-termos-assinatura-diario'] = {
        'task': 'chamados.cobrar_assinaturas_termos',
        'schedule': crontab(
            hour=ITAM_TERMO_ASSINATURA_COBRANCA_HORA,
            minute=ITAM_TERMO_ASSINATURA_COBRANCA_MINUTO,
        ),
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

SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=not DEBUG, cast=bool)
CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=not DEBUG, cast=bool)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = config('CSRF_COOKIE_HTTPONLY', default=False, cast=bool)
SESSION_COOKIE_SAMESITE = config('SESSION_COOKIE_SAMESITE', default='Lax')
CSRF_COOKIE_SAMESITE = config('CSRF_COOKIE_SAMESITE', default='Lax')
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=DJANGO_ENV == 'production', cast=bool)
SECURE_HSTS_SECONDS = config(
    'SECURE_HSTS_SECONDS',
    default=31536000 if DJANGO_ENV == 'production' else 0,
    cast=int,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config(
    'SECURE_HSTS_INCLUDE_SUBDOMAINS',
    default=DJANGO_ENV == 'production',
    cast=bool,
)
SECURE_HSTS_PRELOAD = config('SECURE_HSTS_PRELOAD', default=False, cast=bool)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
X_FRAME_OPTIONS = 'DENY'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s %(levelname)s [%(name)s] %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
        'app_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'itam.log'),
            'maxBytes': LOG_FILE_MAX_BYTES,
            'backupCount': LOG_FILE_BACKUP_COUNT,
            'encoding': 'utf-8',
            'formatter': 'standard',
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'itam-error.log'),
            'maxBytes': LOG_FILE_MAX_BYTES,
            'backupCount': LOG_FILE_BACKUP_COUNT,
            'encoding': 'utf-8',
            'formatter': 'standard',
            'level': 'ERROR',
        },
    },
    'root': {
        'handlers': ['console', 'app_file', 'error_file'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'app_file', 'error_file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console', 'app_file', 'error_file'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console', 'app_file', 'error_file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console', 'app_file', 'error_file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
    },
}

EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=f'{APP_NAME} <noreply@empresa.com>')
ITAM_ADMIN_EMAILS = csv_config('ITAM_ADMIN_EMAILS')
ITAM_CORPORATE_WEBHOOKS_ENABLED = config('ITAM_CORPORATE_WEBHOOKS_ENABLED', default=False, cast=bool)
ITAM_TEAMS_WEBHOOK_URL = config('ITAM_TEAMS_WEBHOOK_URL', default='')
ITAM_SLACK_WEBHOOK_URL = config('ITAM_SLACK_WEBHOOK_URL', default='')
ITAM_WEBHOOK_TIMEOUT_SECONDS = config('ITAM_WEBHOOK_TIMEOUT_SECONDS', default=5, cast=int)

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
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': f'{APP_NAME} API',
    'DESCRIPTION': f'Contrato OpenAPI para integracoes internas do {APP_NAME}.',
    'VERSION': '2026.1',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}

AUDITLOG_INCLUDE_ALL_MODELS = False

ITAM_ESTOQUE_ALERTA_MINIMO = config('ITAM_ESTOQUE_ALERTA_MINIMO', default=5, cast=int)
ITAM_RESERVA_INTELIGENTE_SCORE_MINIMO = config('ITAM_RESERVA_INTELIGENTE_SCORE_MINIMO', default=70, cast=int)
ITAM_PREVISAO_DIAS = config('ITAM_PREVISAO_DIAS', default=30, cast=int)
ITAM_HEARTBEAT_STALE_MINUTES = config('ITAM_HEARTBEAT_STALE_MINUTES', default=10, cast=int)
ITAM_ADMIN_2FA_REQUIRED = config('ITAM_ADMIN_2FA_REQUIRED', default='test' not in sys.argv, cast=bool)
ITAM_TWO_FACTOR_ENCRYPTION_KEY = config('ITAM_TWO_FACTOR_ENCRYPTION_KEY', default='')
ITAM_TWO_FACTOR_ISSUER = config('ITAM_TWO_FACTOR_ISSUER', default=APP_NAME)
ITAM_TWO_FACTOR_VALID_WINDOW = config('ITAM_TWO_FACTOR_VALID_WINDOW', default=1, cast=int)
ITAM_MONITORING_ALERT_COOLDOWN_MINUTES = config('ITAM_MONITORING_ALERT_COOLDOWN_MINUTES', default=30, cast=int)
ITAM_SLA_ETAPA_MINUTOS = config(
    'ITAM_SLA_ETAPA_MINUTOS',
    default='solicitado:240,triagem:240,aguardando_estoque:1440,aguardando_aprovacao:1440,aprovado_para_retirada:480,em_separacao:480,pronto_para_entrega:480',
)
ITAM_SLA_ETAPA_ALERTA_PERCENTUAL = config('ITAM_SLA_ETAPA_ALERTA_PERCENTUAL', default=75, cast=int)
ITAM_TERMO_ASSINATURA_VALIDADE_DIAS = config('ITAM_TERMO_ASSINATURA_VALIDADE_DIAS', default=7, cast=int)
ITAM_API_SHARED_KEY = config('ITAM_API_SHARED_KEY', default='')
ITAM_API_SHARED_KEY_SHA256 = config('ITAM_API_SHARED_KEY_SHA256', default='')
ITAM_API_SERVICE_MATRICULA = config('ITAM_API_SERVICE_MATRICULA', default='')
ITAM_API_AUTH_RATE_LIMIT = config('ITAM_API_AUTH_RATE_LIMIT', default='30/m')
ITAM_API_REQUEST_RATE_LIMIT = config('ITAM_API_REQUEST_RATE_LIMIT', default='120/m')
ITAM_RATE_LIMIT_BYPASS_IPS = csv_config('ITAM_RATE_LIMIT_BYPASS_IPS')
ITAM_TRUSTED_PROXY_IPS = csv_config('ITAM_TRUSTED_PROXY_IPS')
RATELIMIT_VIEW = 'itam.middleware.ratelimited_view'
