import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def required_env(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} must be configured')
    return value


def env_bool(name, default):
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {'true', 'false'}:
        raise ImproperlyConfigured(f'{name} must be true or false')
    return value == 'true'


DEBUG = env_bool('DEBUG', False)
SERVE_MEDIA = env_bool('SERVE_MEDIA', False)
SECRET_KEY = required_env('SECRET_KEY')
JWT_SIGNING_KEY = required_env('JWT_SIGNING_KEY')
DB_PASSWORD = required_env('DB_PASSWORD')
SMARTLOCKER_DEVICE_TOKEN = required_env('SMARTLOCKER_DEVICE_TOKEN')
if len(SMARTLOCKER_DEVICE_TOKEN) < 32:
    raise ImproperlyConfigured('SMARTLOCKER_DEVICE_TOKEN must contain at least 32 characters')
try:
    SMARTLOCKER_DEVICE_TOKEN.encode('ascii')
except UnicodeEncodeError as exc:
    raise ImproperlyConfigured('SMARTLOCKER_DEVICE_TOKEN must be ASCII') from exc

MQTT_BROKER_HOST = required_env('MQTT_BROKER_HOST')
try:
    MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', '1883' if DEBUG else '8883'))
except ValueError as exc:
    raise ImproperlyConfigured('MQTT_BROKER_PORT must be an integer') from exc
if not 1 <= MQTT_BROKER_PORT <= 65535:
    raise ImproperlyConfigured('MQTT_BROKER_PORT must be between 1 and 65535')
MQTT_USERNAME = required_env('MQTT_USERNAME')
MQTT_PASSWORD = required_env('MQTT_PASSWORD')
MQTT_TLS = env_bool('MQTT_TLS', True)
MQTT_CA_CERT = os.getenv('MQTT_CA_CERT', '').strip() or None
MQTT_TOPIC_COMMAND = os.getenv('MQTT_TOPIC_COMMAND', 'smartlocker/{locker_number}/command').strip()
if (
    MQTT_TOPIC_COMMAND.count('{locker_number}') != 1
    or '+' in MQTT_TOPIC_COMMAND
    or '#' in MQTT_TOPIC_COMMAND
    or any(character.isspace() for character in MQTT_TOPIC_COMMAND)
):
    raise ImproperlyConfigured('MQTT_TOPIC_COMMAND must include one {locker_number} and no MQTT wildcards or whitespace')
if not DEBUG and not MQTT_TLS:
    raise ImproperlyConfigured('MQTT_TLS must be enabled when DEBUG is false')

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
    if host.strip()
]
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must contain explicit hostnames and cannot contain *')

MANUAL_PAYMENT_INSTRUCTIONS = os.getenv('MANUAL_PAYMENT_INSTRUCTIONS', '').strip()
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media'))).resolve()
PRIVATE_MEDIA_ROOT = Path(os.getenv('PRIVATE_MEDIA_ROOT', str(BASE_DIR / 'private_media'))).resolve()
if MEDIA_ROOT == PRIVATE_MEDIA_ROOT or MEDIA_ROOT in PRIVATE_MEDIA_ROOT.parents or PRIVATE_MEDIA_ROOT in MEDIA_ROOT.parents:
    raise ImproperlyConfigured('MEDIA_ROOT and PRIVATE_MEDIA_ROOT must be distinct, non-nested directories')
SECURE_SSL_HOST = os.getenv('SECURE_SSL_HOST', '').strip() or None
CACHE_URL = os.getenv('CACHE_URL', 'redis://localhost:6379/1')



INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_extensions',
    
    # Aplikasi Pihak Ketiga
    'rest_framework',
    'drf_spectacular',
    'rest_framework_simplejwt',
    
    # Aplikasi Saya
    'apps.users',
    'apps.lockers',
    'apps.marketplace',
    'apps.iot',
    'apps.notifications',
    'apps.package_center',
    'apps.facerecognition',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'smartlocker.urls'
WSGI_APPLICATION = 'smartlocker.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# --- Database (PostgreSQL) ---
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'smartlocker'),
        'USER': os.getenv('DB_USER', 'smartlocker_admin'),
        'PASSWORD': DB_PASSWORD,
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Jakarta'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
MEDIA_URL = '/media/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 3600 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': CACHE_URL,
    },
}
# Fail-closed: only trust X-Forwarded-Proto when explicitly opted in.
# Safe only behind a trusted reverse proxy that overwrites/strips the header
# and where the app is not directly reachable (otherwise clients can spoof it).
TRUST_X_FORWARDED_PROTO = env_bool('TRUST_X_FORWARDED_PROTO', False)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if TRUST_X_FORWARDED_PROTO else None



AUTH_USER_MODEL = 'users.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ('rest_framework_simplejwt.authentication.JWTAuthentication',),
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_RATES': {
        'user_login': '10/min',
        'user_registration': '10/hour',
        'face_training': '20/hour',
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "SIGNING_KEY": JWT_SIGNING_KEY,
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'SmartLocker API',
    'DESCRIPTION': 'API schema for SmartLocker backend integration.',
    'VERSION': '2.0.0',
}

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Jakarta'


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}