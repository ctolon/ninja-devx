"""Application plugins: bundle cross-cutting setup for a ``NinjaAPI``.

A plugin contributes middleware, error rules and startup work in one object, so an
application installs a coherent unit instead of wiring each piece::

    class Observability(APIPlugin):
        def middleware(self) -> Sequence[Middleware]:
            return [RequestIDMiddleware(), OpenTelemetryMetricsMiddleware()]

        def errors(self) -> ErrorMap | None:
            return ErrorMap.django_defaults()

        def setup(self, api: NinjaAPI) -> None:
            api.title = "My API"

    install(api, [Observability()])

Like ``use_middleware``, ``install`` runs before routers are mounted. It is distinct from
:class:`~ninja_devx.routing.plugins.ControllerPlugin`, which extends individual
controllers/operations.
"""

from __future__ import annotations

from collections.abc import Sequence

from ninja import NinjaAPI

from .http.errors import ErrorMap
from .http.middleware import Middleware, use_middleware

__all__ = ["APIPlugin", "install"]


class APIPlugin:
    """Base class for an application plugin. Override the parts you need."""

    def middleware(self) -> Sequence[Middleware]:
        """Middleware applied to every operation of the API."""
        return ()

    def errors(self) -> ErrorMap | None:
        """Exception rules installed on the API."""
        return None

    def setup(self, api: NinjaAPI) -> None:
        """Runs after middleware and error rules are installed."""


def install(api: NinjaAPI, plugins: Sequence[APIPlugin], /) -> None:
    """Install ``plugins`` on ``api``: error rules, middleware, then ``setup``.

    Error rules of all plugins are merged into one ``ErrorMap`` before installation, so a
    rule for a subclass wins over a base-class rule regardless of plugin order. Call it
    before mounting routers.

    :param api: The ``NinjaAPI`` to configure.
    :param plugins: Plugins applied in order.
    """
    errors = ErrorMap()
    middlewares: list[Middleware] = []
    for plugin in plugins:
        rules = plugin.errors()
        if rules is not None:
            errors = errors | rules
        middlewares.extend(plugin.middleware())
    if errors:
        errors.install(api)
    if middlewares:
        use_middleware(api, *middlewares)
    for plugin in plugins:
        plugin.setup(api)
