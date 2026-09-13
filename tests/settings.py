import os
from urllib.parse import urlparse

SECRET_KEY = "ninja-devx-tests"
DEBUG = False
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "ninja_devx",
    "ninja_devx.contrib.grants",
    "ninja_devx.contrib.apikeys",
    "ninja_devx.contrib.audit",
    "ninja_devx.contrib.webhooks",
    "guardian",
    "tests.testapp",
]
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "ninja_devx.contrib.grants.backends.GrantBackend",
    "guardian.backends.ObjectPermissionBackend",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
if _url := os.environ.get("TEST_DATABASE_URL"):  # postgresql://user:password@host:5432/name
    _parsed = urlparse(_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _parsed.path.lstrip("/"),
            "USER": _parsed.username or "",
            "PASSWORD": _parsed.password or "",
            "HOST": _parsed.hostname or "",
            "PORT": str(_parsed.port or 5432),
        }
    }
# A separate database exercises alias-specific transactions and outbox writes locally.
DATABASES["other"] = dict(DATABASES["default"])
if DATABASES["other"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["other"]["NAME"] += "_other"

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "tests.urls"
USE_TZ = True
ANONYMOUS_USER_NAME = None  # django-guardian: no anonymous user row
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
