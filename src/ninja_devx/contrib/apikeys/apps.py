from django.apps import AppConfig


class APIKeysConfig(AppConfig):
    name = "ninja_devx.contrib.apikeys"
    label = "ninja_devx_apikeys"
    verbose_name = "API keys"
    default_auto_field = "django.db.models.BigAutoField"
