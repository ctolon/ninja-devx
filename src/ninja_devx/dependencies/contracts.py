"""Resolver interfaces shared by dependency adapters."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
)
from typing import (
    Protocol,
    TypeAlias,
    TypeVar,
    runtime_checkable,
)

from django.http import HttpRequest

T = TypeVar("T")


T_co = TypeVar("T_co", covariant=True)


Factory: TypeAlias = Callable[..., T_co]


@runtime_checkable
class Resolver(Protocol):
    """Builds objects synchronously."""

    def resolve(self, key: type[T], /) -> T: ...


@runtime_checkable
class RequestScopeProvider(Protocol):
    """Opens a scope per request; objects scoped to it are released when it closes."""

    def request_scope(self, request: HttpRequest, /) -> AbstractContextManager[Resolver]: ...


@runtime_checkable
class AsyncResolver(Protocol):
    async def aresolve(self, key: type[T], /) -> T: ...


@runtime_checkable
class AsyncRequestScopeProvider(Protocol):
    """Async counterpart of ``RequestScopeProvider``, used by ``async`` operations."""

    def arequest_scope(
        self, request: HttpRequest, /
    ) -> AbstractAsyncContextManager[AsyncResolver]: ...


@runtime_checkable
class CheckableContainer(Protocol):
    """A container that can tell at startup whether it will be able to build ``key``.

    ``as_router()`` calls ``check`` for the controller and every ``Inject[T]``; raise
    ``DependencyResolutionError`` with a helpful message when it cannot.
    """

    def check(self, key: type[object], /, *, asynchronous: bool = False) -> None: ...


ContainerLike: TypeAlias = Resolver | RequestScopeProvider | AsyncRequestScopeProvider
