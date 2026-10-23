import os
from pathlib import Path
from urllib.parse import urlparse, unquote
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
SITE_VERSION = (BASE_DIR/'VERSION').read_text(encoding='utf-8').strip()
DEBUG = os.getenv('DEBUG', '1') == '1'
DEPLOY_SCHEME = os.getenv('DEPLOY_SCHEME', 'https')
if DEPLOY_SCHEME not in ('http', 'https'):
    raise ImproperlyConfigured('DEPLOY_SCHEME must be http or https')
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError('Set SECRET_KEY when DEBUG=0')
    # Keep local sessions valid between restarts without committing a secret.
    key_file = BASE_DIR / '.secret_key'
    if not key_file.exists():
        from django.core.management.utils import get_random_secret_key
        key_file.write_text(get_random_secret_key(), encoding='utf-8')
    SECRET_KEY = key_file.read_text(encoding='utf-8')
ALLOWED_HOSTS = [x.strip() for x in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',') if x.strip()]
INSTALLED_APPS = ['college.apps.CollegeAdminConfig', 'django.contrib.auth', 'django.contrib.contenttypes',
                  'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'schedule']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware', 'schedule.middleware.ApiGuard']
ROOT_URLCONF = 'college.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
              'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request',
              'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages', 'college.context.release']}}]
WSGI_APPLICATION = 'college.wsgi.application'
# WAL keeps readers independent of writers; IMMEDIATE avoids read-to-write lock upgrades.
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3',
    'OPTIONS': {'timeout': 20,'transaction_mode':'IMMEDIATE','init_command':'PRAGMA journal_mode=WAL;'}}}
if os.getenv('DATABASE_URL'):
    db = urlparse(os.environ['DATABASE_URL'])
    DATABASES['default'] = {'ENGINE': 'django.db.backends.postgresql', 'NAME': db.path.lstrip('/'),
        'USER': unquote(db.username or ''), 'PASSWORD': unquote(db.password or ''), 'HOST': db.hostname, 'PORT': db.port or 5432}
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.' + name} for name in
    ['UserAttributeSimilarityValidator', 'MinimumLengthValidator', 'CommonPasswordValidator', 'NumericPasswordValidator']]
LANGUAGE_CODE = 'ru'
TIME_ZONE = os.getenv('TIME_ZONE', 'Europe/Moscow')
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/media/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
# Browser HSTS preload is a separate domain-owner opt-in, not a deployment requirement.
SILENCED_SYSTEM_CHECKS = ['security.W021']
CSRF_FAILURE_VIEW = 'schedule.views.csrf_failure'
if os.getenv('TRUST_PROXY', '0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = not DEBUG and DEPLOY_SCHEME == 'https'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_SSL_REDIRECT = SESSION_COOKIE_SECURE
SECURE_HSTS_SECONDS = 31536000 if SESSION_COOKIE_SECURE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SESSION_COOKIE_SECURE
if not DEBUG and DEPLOY_SCHEME == 'http':
    # Explicit test transport: suppress only checks requiring TLS, never CSRF checks.
    SILENCED_SYSTEM_CHECKS += ['security.W004', 'security.W008', 'security.W012', 'security.W016']
    # Cookies are not isolated by port; avoid collisions with other apps on this IP.
    SESSION_COOKIE_NAME = 'schedule_sessionid'
    CSRF_COOKIE_NAME = 'schedule_csrftoken'
    SECURE_CROSS_ORIGIN_OPENER_POLICY = None
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if x.strip()]
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'handlers': {'console': {'class': 'logging.StreamHandler'}},
           'root': {'handlers': ['console'], 'level': 'INFO'}}
