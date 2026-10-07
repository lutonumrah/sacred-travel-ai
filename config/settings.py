"""
Django settings for Scared Travel AI Operating System.
"""

import os
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Off unless explicitly enabled, so a missing .env can never serve debug pages.
DEBUG = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes")

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "django-insecure-scared-travel-ai-dev-only-change-me"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "corsheaders",
    # Local apps
    "core",
    "accounts",
    "websites",
    "inventory",
    "crm",
    "conversations",
    "bookings",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.nav_context",
                "core.context_processors.notification_badge",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

def _database_config(url):
    """Minimal DATABASE_URL support: sqlite by default, postgres when given."""
    if url.startswith("postgres"):
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
        }
    name = url.split("sqlite:///")[-1] if url.startswith("sqlite") else "db.sqlite3"
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / name,
        # WAL lets readers run while a write is in flight; without it a
        # multi-worker gunicorn serialises on the file and raises
        # "database is locked" under even light concurrency.
        "OPTIONS": {
            "timeout": 20,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
        },
    }


DATABASES = {"default": _database_config(os.getenv("DATABASE_URL", "sqlite:///db.sqlite3"))}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "accounts.User"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:overview"
LOGOUT_REDIRECT_URL = "accounts:login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "core.api.exception_handler",
    # nginx is the one proxy in front of gunicorn: the client IP is the last
    # X-Forwarded-For entry. Set NUM_PROXIES=0 when serving without nginx.
    "NUM_PROXIES": int(os.getenv("NUM_PROXIES", "1")),
}

# Per-IP limits for the anonymous widget and payment-link endpoints
# (core.throttling). DRF rate syntax: "<count>/<second|minute|hour|day>".
PUBLIC_API_THROTTLE_RATES = {
    "widget_chat": os.getenv("THROTTLE_WIDGET_CHAT", "30/minute"),
    # History + polling; the widget polls every 3–30s while a human is involved.
    "widget_poll": os.getenv("THROTTLE_WIDGET_POLL", "120/minute"),
    "widget_book": os.getenv("THROTTLE_WIDGET_BOOK", "20/hour"),
    "public_pay": os.getenv("THROTTLE_PUBLIC_PAY", "60/hour"),
}

# --- CORS -----------------------------------------------------------------
# Only the public widget / pay-link APIs answer cross-origin requests, and only
# for origins on a registered website's domain (see core/cors.py). The staff
# API and dashboard never send CORS headers.
CORS_ALLOW_ALL_ORIGINS = False
CORS_URLS_REGEX = r"^/api/v1/(conversations/widget|pay)/"

# --- Behind a TLS-terminating proxy (nginx) ------------------------------
# CSRF_TRUSTED_ORIGINS must list the full scheme+host the dashboard is served
# from, or Django rejects every form POST once DEBUG is off.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

if os.getenv("USE_X_FORWARDED_PROTO", "False").lower() in ("1", "true", "yes"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- Payments (Razorpay) -------------------------------------------------
# With no keys configured the gateway runs in simulation mode: orders are
# created locally so the whole booking flow stays testable offline.
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

BOOKING_TAX_PERCENT = Decimal(os.getenv("BOOKING_TAX_PERCENT", "5"))
# How long a customer's /pay/<token>/ link works. Staff can issue a new one.
PAYMENT_LINK_TTL_DAYS = int(os.getenv("PAYMENT_LINK_TTL_DAYS", "7"))

# --- AI chat engine ------------------------------------------------------
# The conversation engine always runs its deterministic rule layer (intent and
# requirement extraction + inventory matching). When an API key is present it
# additionally asks Claude or Gemini to write the customer-facing reply.
# Provider, model and keys are normally set by an admin under AI Settings in the
# dashboard; these variables are the fallback when nothing is saved there.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-opus-5-5")
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() in ("1", "true", "yes")
AI_MAX_HISTORY = int(os.getenv("AI_MAX_HISTORY", "20"))
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "30"))

# --- Email ---------------------------------------------------------------
# With no EMAIL_HOST, mail is printed to the container log instead of sent, so
# nothing breaks before SMTP is configured. Every send goes through
# core.emails, which logs failures instead of raising.
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "False").lower() in ("1", "true", "yes")
if EMAIL_USE_SSL:
    # Django refuses both at once; implicit TLS (port 465) wins when asked for.
    EMAIL_USE_TLS = False
# Mail is sent inside the request; a hung SMTP server must not hold a worker.
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Scared Travel <no-reply@localhost>")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
# Absolute base for links in emails (payment links, password reset, dashboard
# links). Scheme + host, no trailing slash. Never derived from the request's
# Host header, which a client controls.
SITE_URL = os.getenv("SITE_URL", "http://localhost:8000").rstrip("/")

# --- Scheduled jobs and backups (manage.py run_scheduled_jobs) -----------
# Nightly SQLite snapshot: taken on the first scheduler run at or after this
# local hour (TIME_ZONE), once per day, keeping the newest BACKUP_KEEP_DAYS.
# Defaults to a `backups/` folder beside the database file (/app/data/backups
# in Docker, on the same volume as the database).
BACKUP_DIR = os.getenv("BACKUP_DIR", "")
BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "3"))
BACKUP_KEEP_DAYS = int(os.getenv("BACKUP_KEEP_DAYS", "14"))
BACKUP_ENABLED = os.getenv("BACKUP_ENABLED", "True").lower() in ("1", "true", "yes")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    # Container logs are the only log store; app warnings and the scheduler's
    # job lines end up in `docker compose logs`.
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
    # 4xx responses are routine (expired links, permission checks); keep 5xx.
    "loggers": {"django.request": {"level": "ERROR"}},
}
