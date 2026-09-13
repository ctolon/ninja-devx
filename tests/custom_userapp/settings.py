from tests.settings import *  # noqa: F403
from tests.settings import DATABASES, INSTALLED_APPS

INSTALLED_APPS = [*INSTALLED_APPS, "tests.custom_userapp"]
AUTH_USER_MODEL = "custom_userapp.UUIDUser"
ROOT_URLCONF = "tests.custom_userapp.urls"
DATABASES = {alias: dict(config) for alias, config in DATABASES.items()}
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"]["NAME"] += "_uuid"
