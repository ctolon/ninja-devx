from django.apps import AppConfig


class GrantsConfig(AppConfig):
    name = "ninja_devx.contrib.grants"
    label = "ninja_devx_grants"
    verbose_name = "Object permission grants"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from ...security.object_permissions import register_object_permission_backend
        from .backends import GrantsBackend

        register_object_permission_backend("grants", GrantsBackend)
