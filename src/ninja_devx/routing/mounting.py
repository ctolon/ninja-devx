"""Mount many controllers at once, e.g. per API version."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Unpack

from ninja import NinjaAPI, Router

from ..dependencies.container import ContainerLike
from .controller import Controller, ControllerOptions, Scope

__all__ = ["Mount", "mount"]


@dataclass(frozen=True, slots=True)
class Mount:
    """Per-route overrides for ``mount``."""

    controller: type[Controller]
    """The controller class."""
    container: ContainerLike | None = None
    """Container for this entry (default: the ``mount()`` container)."""
    scope: Scope | None = None
    """Scope for this entry (default: the ``mount()`` scope)."""
    options: ControllerOptions = field(default_factory=lambda: ControllerOptions())
    """``ControllerOptions`` for this entry, over the ``mount()`` options."""


def mount(
    target: NinjaAPI | Router,
    routes: Mapping[str, type[Controller] | Mount],
    *,
    prefix: str = "",
    container: ContainerLike | None = None,
    scope: Scope | None = None,
    **options: Unpack[ControllerOptions],
) -> dict[str, Router]:
    """Build and add a router per route; shared ``container``, ``scope`` and ``options``.

    ::

        mount(api, {"/articles": ArticleController, "/authors": AuthorController}, container=c)
        mount(api, V1_ROUTES, prefix="/v1", deprecated=True)
        mount(api, V2_ROUTES, prefix="/v2")

    With a ``prefix``, operation ids and URL names get a matching prefix (``v1_``) so
    OpenAPI ids and URL reversing stay unique when the same controllers are mounted twice.

    :param target: The ``NinjaAPI`` or ``Router`` to mount on.
    :param routes: ``{prefix: Controller}`` or ``{prefix: Mount(Controller, ...)}``.
    :param prefix: Prepended to every route; also prefixes operation ids and URL names (versioning).
    :param container: Shared container for every entry.
    :param scope: Shared scope for every entry.
    :param options: ``ControllerOptions`` for every entry; ``errors=`` is also installed on a
        ``NinjaAPI``.
    """
    if prefix:
        slug = re.sub(r"\W+", "_", prefix.strip("/")).strip("_")
        options.setdefault("operation_id_prefix", f"{slug}_")
        options.setdefault("url_name_prefix", slug)
    url_name_prefix = options.get("url_name_prefix")
    errors = options.get("errors")
    if errors is not None and isinstance(target, NinjaAPI):
        errors.install(target)  # also covers plain function views on the API

    routers: dict[str, Router] = {}
    for path, route in routes.items():
        entry = route if isinstance(route, Mount) else Mount(route)
        merged: ControllerOptions = {**options, **entry.options}
        router = entry.controller.as_router(
            container=entry.container or container, scope=entry.scope or scope, **merged
        )
        full_path = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path
        if isinstance(target, NinjaAPI):
            target.add_router(full_path, router, url_name_prefix=url_name_prefix)
        else:
            target.add_router(full_path, router)
        routers[full_path] = router
    return routers
