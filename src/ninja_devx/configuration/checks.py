"""Django system checks for controllers: ``manage.py check`` reports them in CI.

Checked controllers are those behind the APIs listed in ``NINJA_DEVX["CHECK_APIS"]``
(dotted paths to ``NinjaAPI`` instances), or, when it is empty, every router built by
``as_router()`` once the URLconf is loaded.

Messages come from ``Controller.checks()`` (overridable) and from plugins that define
``checks(controller)``. Startup errors that make a controller unusable are still raised
by ``as_router()`` itself.

========================  ===========================================================
``ninja_devx.E001``        a ``CHECK_APIS`` entry cannot be imported or is not an API
``ninja_devx.E002``        ``search_fields``/``filter_fields``/``ordering_fields`` name
                          a missing model field
``ninja_devx.W003``        an output schema field is neither a model field, a model
                          attribute nor resolved by the schema
``ninja_devx.E004``        ``service_class`` needs constructor arguments but the
                          controller is mounted without a container
========================  ===========================================================
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from django.core.checks import CheckMessage, Error, register
from django.utils.module_loading import import_string
from ninja import NinjaAPI, Router

from ..routing.controller import BuiltRouter, built_router, built_routers
from .settings import SETTINGS_NAME, get_settings

if TYPE_CHECKING:
    from django.apps import AppConfig

__all__ = ["CHECKS", "check_controllers"]

CHECKS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "ninja_devx.E001": (
            "error",
            "A ``CHECK_APIS`` entry cannot be imported or is not a ``NinjaAPI``.",
        ),
        "ninja_devx.E002": (
            "error",
            "``search_fields``, ``filter_fields``, ``ordering_fields`` or "
            "``default_ordering`` names a "
            "missing model field.",
        ),
        "ninja_devx.W003": (
            "warning",
            "An output schema field is not a model field or attribute, and the schema does not "
            "resolve it.",
        ),
        "ninja_devx.E004": (
            "error",
            "``service_class`` needs constructor arguments, but the controller has no container.",
        ),
        "ninja_devx.W005": (
            "warning",
            "An output field that ``VisibleTo`` can hide is required instead of optional.",
        ),
    }
)
"""Every system check id with its level and meaning (also rendered in the docs)."""

TAG = "ninja_devx"


@register(TAG)
def check_controllers(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: object
) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    built: list[BuiltRouter] = []
    check_apis = get_settings().check_apis
    if check_apis:
        for path in check_apis:
            api = _import_api(path, messages)
            if api is not None:
                built.extend(_api_controllers(api))
    else:
        _load_urlconf()
        built = built_routers()
    seen: set[type[object]] = set()
    for entry in built:
        if entry.controller in seen:
            continue
        seen.add(entry.controller)
        messages.extend(entry.controller.checks(entry.container))
        for plugin in entry.plugins:
            plugin_checks: Callable[[type[object]], Iterable[CheckMessage]] | None = getattr(
                plugin, "checks", None
            )
            if plugin_checks is not None:
                messages.extend(plugin_checks(entry.controller))
    return messages


def _import_api(path: str, messages: list[CheckMessage]) -> NinjaAPI | None:
    try:
        api: object = import_string(path)
    except ImportError as exc:
        messages.append(
            Error(
                f"{SETTINGS_NAME}['CHECK_APIS'] entry {path!r} cannot be imported: {exc}",
                id="ninja_devx.E001",
            )
        )
        return None
    if not isinstance(api, NinjaAPI):
        messages.append(
            Error(
                f"{SETTINGS_NAME}['CHECK_APIS'] entry {path!r} is not a NinjaAPI",
                hint="Point it at the NinjaAPI instance, e.g. 'config.api.api'.",
                id="ninja_devx.E001",
            )
        )
        return None
    return api


def _api_controllers(api: NinjaAPI) -> list[BuiltRouter]:
    found: list[BuiltRouter] = []
    mounted: list[tuple[str, Router]] = getattr(api, "_routers", [])
    pending: list[Router] = [router for _, router in mounted]
    while pending:
        router = pending.pop()
        if (entry := built_router(router)) is not None:
            found.append(entry)
        children: list[tuple[str, Router, object]] = getattr(router, "_routers", [])
        pending.extend(child for _, child, _ in children)
    return found


def _load_urlconf() -> None:
    from django.urls import get_resolver

    with suppress(Exception):  # Django's own URL checks report a broken URLconf
        get_resolver().url_patterns  # noqa: B018 - imports the URLconf (and the APIs)
