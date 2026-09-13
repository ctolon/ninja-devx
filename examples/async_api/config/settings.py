from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "example-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "ninja_devx",
    "orders",
]
ROOT_URLCONF = "config.urls"
# A file database: async code runs ORM calls on another thread, which an in-memory
# SQLite database would not share.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(__file__).resolve().parent.parent / "db.sqlite3",
        "TEST": {"NAME": Path(__file__).resolve().parent.parent / "test.sqlite3"},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
ASGI_APPLICATION = "config.asgi.application"

NINJA_DEVX = {
    "ASYNC_MODE": "async",  # every mode="auto" model controller registers its async operations
    "ASYNC_FETCH_MODE": "raise",  # lazy relation loads fail loudly (Django 6.1+)
    "WARN_BLOCKING_MS": 100,  # sync hooks or injected values blocking the loop are reported
    "CHECK_APIS": ["config.urls.api"],
}

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ]
        },
    }
]
LOGIN_REDIRECT_URL = "/api/docs"
LOGOUT_REDIRECT_URL = "/accounts/login/"
