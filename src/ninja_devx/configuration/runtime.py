"""Mutable state owned by an installed Django application, never by a module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from django.apps import apps

if TYPE_CHECKING:
    from ninja import Router

    from ..routing.controller import BuiltRouter
    from .settings import ResolvedSettings


@dataclass
class RuntimeState:
    settings: ResolvedSettings | None = None
    routers: WeakKeyDictionary[Router, BuiltRouter] = field(
        default_factory=lambda: WeakKeyDictionary["Router", "BuiltRouter"]()
    )


def runtime_state() -> RuntimeState | None:
    """Return this app registry's state; core-less use requires no registry."""
    if not apps.apps_ready or not apps.is_installed("ninja_devx"):
        return None
    from ..apps import NinjaDevXConfig

    config = apps.get_app_config("ninja_devx")
    return config.runtime if isinstance(config, NinjaDevXConfig) else None
