from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "example-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "ninja_devx",
    "ninja_devx.contrib.audit",
    "ninja_devx.contrib.grants",
    "tracker",
]
ROOT_URLCONF = "config.urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

NINJA_DEVX = {
    "TENANT_RESOLVER": "tracker.tenancy.workspace_of",
    "THROTTLE_RATES": {"issue-writes": "30/min"},
    "ERROR_FORMAT": "problem+json",
    "CHECK_APIS": ["config.urls.api"],
}
