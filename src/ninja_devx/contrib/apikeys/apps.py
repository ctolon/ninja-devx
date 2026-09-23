from django.apps import AppConfig
from django.http import HttpRequest


class APIKeysConfig(AppConfig):
    name = "ninja_devx.contrib.apikeys"
    label = "ninja_devx_apikeys"
    verbose_name = "API keys"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from ...http.requestlog import register_api_key_reader
        from .auth import current_api_key

        def api_key_prefix(request: HttpRequest) -> str | None:
            key = current_api_key(request)
            return None if key is None else key.prefix

        register_api_key_reader(api_key_prefix)
