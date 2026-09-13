"""Use an `svcs <https://svcs.hynek.me>`_ registry with controllers.

Every request gets its own ``svcs.Container`` (closed, with cleanups, when the
request ends) in which ``HttpRequest`` is registered as a local value::

    registry = svcs.Registry()
    registry.register_factory(Database, connect)
    api.add_router("/users", UserController.as_router(container=SvcsResolver(registry)))

svcs containers are per request, so controllers must use ``Scope.REQUEST``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TypeVar

import svcs
from django.http import HttpRequest

__all__ = ["SvcsResolver"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _Scope:
    container: svcs.Container

    def resolve(self, key: type[T], /) -> T:
        return self.container.get(key)

    async def aresolve(self, key: type[T], /) -> T:
        return await self.container.aget(key)


@dataclass(frozen=True, slots=True)
class SvcsResolver:
    registry: svcs.Registry
    """The svcs registry."""

    @contextmanager
    def request_scope(self, request: HttpRequest, /) -> Generator[_Scope]:
        with svcs.Container(self.registry) as container:
            container.register_local_value(HttpRequest, request)  # pyright: ignore[reportUnknownMemberType]
            yield _Scope(container)

    @asynccontextmanager
    async def arequest_scope(self, request: HttpRequest, /) -> AsyncGenerator[_Scope]:
        async with svcs.Container(self.registry) as container:
            container.register_local_value(HttpRequest, request)  # pyright: ignore[reportUnknownMemberType]
            yield _Scope(container)
