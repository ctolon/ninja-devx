from types import ModuleType

from django.apps import AppConfig

from .configuration.runtime import RuntimeState


class NinjaDevXConfig(AppConfig):
    """Add ``"ninja_devx"`` to ``INSTALLED_APPS`` for the management commands."""

    name = "ninja_devx"
    verbose_name = "Ninja DevX"

    def __init__(self, app_name: str, app_module: ModuleType) -> None:
        super().__init__(app_name, app_module)
        self.runtime = RuntimeState()

    def ready(self) -> None:
        from .configuration.checks import check_controllers

        del check_controllers  # imported to register the system checks
