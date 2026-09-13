"""Use a `dishka <https://dishka.readthedocs.io>`_ container with controllers.

Every request opens a dishka scope (``Scope.REQUEST`` by default) in which
``HttpRequest`` is available as context; declare it with
``from_context(provides=HttpRequest, scope=Scope.REQUEST)``::

    provider = AppProvider()
    provide_controllers(provider, [UserController, OrderController])

    resolver = DishkaResolver(
        make_container(provider),
        async_container=make_async_container(provider),   # only for async operations
    )
    api.add_router("/users", UserController.as_router(container=resolver))

One resolver serves sync and async operations. ``as_router()`` fails at startup when
the controller (or an ``Inject[T]``) is not provided, instead of on the first request.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator, Iterable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TypeVar

from dishka import AsyncContainer, Container, Provider, Scope
from dishka.entities.component import DEFAULT_COMPONENT
from dishka.entities.key import DependencyKey
from dishka.entities.scope import BaseScope
from dishka.registry import Registry
from django.http import HttpRequest

from ..exceptions import DependencyResolutionError

__all__ = ["AsyncDishkaResolver", "DishkaResolver", "provide_controllers"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _Scope:
    container: Container

    def resolve(self, key: type[T], /) -> T:
        return self.container.get(key)


@dataclass(frozen=True, slots=True)
class _AsyncScope:
    container: AsyncContainer

    async def aresolve(self, key: type[T], /) -> T:
        return await self.container.get(key)


@dataclass(frozen=True, slots=True)
class DishkaResolver:
    """Sync operations use ``container``; async ones ``async_container`` when given."""

    container: Container
    """The sync dishka container."""
    scope: BaseScope = Scope.REQUEST
    """dishka scope opened per request."""
    async_container: AsyncContainer | None = None
    """Async container for async operations (optional)."""

    def resolve(self, key: type[T], /) -> T:
        """Resolve from the root container (singleton controllers)."""
        return self.container.get(key)

    @contextmanager
    def request_scope(self, request: HttpRequest, /) -> Generator[_Scope]:
        with self.container(context={HttpRequest: request}, scope=self.scope) as child:
            yield _Scope(child)

    @asynccontextmanager
    async def arequest_scope(self, request: HttpRequest, /) -> AsyncGenerator[_AsyncScope]:
        if self.async_container is None:
            raise DependencyResolutionError(  # pragma: no cover - rejected by check()
                "DishkaResolver needs async_container= for async operations"
            )
        async with self.async_container(context={HttpRequest: request}, scope=self.scope) as child:
            yield _AsyncScope(child)

    def check(self, key: type[object], /, *, asynchronous: bool = False) -> None:
        if asynchronous and self.async_container is not None:
            _check(self.async_container.registry, key)
        else:  # async operations without async_container use the sync one in a thread
            _check(self.container.registry, key)


@dataclass(frozen=True, slots=True)
class AsyncDishkaResolver:
    """An async-only resolver: every operation using it must be ``async``."""

    container: AsyncContainer
    """The async dishka container."""
    scope: BaseScope = Scope.REQUEST
    """dishka scope opened per request."""

    @asynccontextmanager
    async def arequest_scope(self, request: HttpRequest, /) -> AsyncGenerator[_AsyncScope]:
        async with self.container(context={HttpRequest: request}, scope=self.scope) as child:
            yield _AsyncScope(child)

    def check(self, key: type[object], /, *, asynchronous: bool = False) -> None:
        _check(self.container.registry, key)


def provide_controllers(
    provider: Provider, controllers: Iterable[type[object]], *, scope: BaseScope = Scope.REQUEST
) -> None:
    """Register controller classes with ``provider`` (their ``__init__`` is autowired).

    :param provider: The dishka provider to register on.
    :param controllers: Controller classes (their ``__init__`` is autowired).
    :param scope: dishka scope of the controllers.
    """
    for controller in controllers:
        provider.provide(controller, scope=scope)


def _check(registry: Registry, key: type[object]) -> None:
    dependency = DependencyKey(key, DEFAULT_COMPONENT)
    current: Registry | None = registry
    while current is not None:
        if current.get_factory(dependency) is not None:
            return
        current = current.child_registry
    name = getattr(key, "__qualname__", repr(key))
    raise DependencyResolutionError(
        f"dishka cannot provide {name}: add it to a provider "
        f"(provide_controllers(provider, [{name}]) for controllers)"
    )
