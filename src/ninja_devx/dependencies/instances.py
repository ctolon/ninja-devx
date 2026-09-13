"""How controller instances (and their DI scope) are provided per call."""

from __future__ import annotations

import functools
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import TYPE_CHECKING, Final

from django.http import HttpRequest

from .._internal.compat import signature
from ..exceptions import ControllerConfigError
from .container import (
    AsyncRequestScopeProvider,
    AsyncResolver,
    ContainerLike,
    RequestScopeProvider,
    Resolver,
)

if TYPE_CHECKING:
    from ..routing.controller import Controller

__all__ = ["InstanceProvider", "Invocation", "Scope", "get_invocation", "instance_provider"]

_REQUEST_ATTR: Final = "_ninja_devx_invocation"


class Scope(StrEnum):
    """How long a controller instance lives."""

    REQUEST = "request"
    """A new instance per request (like Django's class-based views)."""

    SINGLETON = "singleton"
    """One instance per ``as_router()`` call, created eagerly. Must be stateless."""


class Invocation:
    """One call of an operation: the controller, the request and its DI scope."""

    __slots__ = ("async_resolver", "controller", "request", "resolver")

    def __init__(
        self,
        controller: Controller,
        request: HttpRequest,
        resolver: Resolver | None = None,
        async_resolver: AsyncResolver | None = None,
    ) -> None:
        self.controller = controller
        self.request = request
        self.resolver = resolver
        self.async_resolver = async_resolver


def get_invocation(request: HttpRequest) -> Invocation | None:
    """The current invocation (controller and DI scope) of ``request``."""
    invocation: Invocation | None = getattr(request, _REQUEST_ATTR, None)
    return invocation


class _Ready:
    __slots__ = ("invocation",)

    def __init__(self, invocation: Invocation) -> None:
        self.invocation = invocation

    def __enter__(self) -> Invocation:
        setattr(self.invocation.request, _REQUEST_ATTR, self.invocation)
        return self.invocation

    def __exit__(self, *exc_info: object) -> None:
        return None


class _Scoped:
    """Enters the container's request scope and resolves the controller in it."""

    __slots__ = ("manager", "provider", "request")

    def __init__(
        self,
        provider: InstanceProvider,
        request: HttpRequest,
        manager: AbstractContextManager[Resolver],
    ) -> None:
        self.provider = provider
        self.request = request
        self.manager = manager

    def __enter__(self) -> Invocation:
        scope = self.manager.__enter__()
        controller = self.provider.singleton or scope.resolve(self.provider.cls)
        asynchronous = scope if isinstance(scope, AsyncResolver) else None
        invocation = Invocation(controller, self.request, scope, asynchronous)
        setattr(self.request, _REQUEST_ATTR, invocation)
        return invocation

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self.manager.__exit__(exc_type, exc_value, traceback)


@dataclass(frozen=True, slots=True)
class InstanceProvider:
    """Creates (or reuses) the controller for one call, within a DI scope if available."""

    cls: type[Controller]
    container: ContainerLike | None
    singleton: Controller | None

    @property
    def supports_sync(self) -> bool:
        return (
            self.singleton is not None
            or self.container is None
            or isinstance(self.container, RequestScopeProvider | Resolver)
        )

    @property
    def has_resolver(self) -> bool:
        return self.container is not None

    def factory(self) -> Callable[[], Controller] | None:
        """A plain callable when no scope has to be entered (the fast path)."""
        if self.singleton is not None and not isinstance(self.container, RequestScopeProvider):
            singleton = self.singleton
            return lambda: singleton
        if self.container is None:
            return self.cls
        if isinstance(self.container, RequestScopeProvider):
            return None
        if isinstance(self.container, Resolver):
            return functools.partial(self.container.resolve, self.cls)
        return None

    def sync(self, request: HttpRequest) -> AbstractContextManager[Invocation]:
        container = self.container
        if container is None:
            return _Ready(Invocation(self.singleton or self.cls(), request))
        if isinstance(container, RequestScopeProvider):
            return _Scoped(self, request, container.request_scope(request))
        if isinstance(container, Resolver):
            controller = self.singleton or container.resolve(self.cls)
            return _Ready(Invocation(controller, request, container))
        raise ControllerConfigError(  # pragma: no cover - rejected by as_router()
            f"{container!r} only supports async operations"
        )

    def asynchronous(self, request: HttpRequest) -> AbstractAsyncContextManager[Invocation]:
        return self._async(request)

    @asynccontextmanager
    async def _async(self, request: HttpRequest) -> AsyncGenerator[Invocation]:
        container = self.container
        if isinstance(container, AsyncRequestScopeProvider):
            async with container.arequest_scope(request) as scope:
                controller = (
                    self.singleton if self.singleton is not None else await scope.aresolve(self.cls)
                )
                sync_scope = scope if isinstance(scope, Resolver) else None
                invocation = Invocation(controller, request, sync_scope, scope)
                setattr(request, _REQUEST_ATTR, invocation)
                yield invocation
        else:
            with self.sync(request) as invocation:
                yield invocation


def instance_provider(
    cls: type[Controller], container: ContainerLike | None, scope: Scope
) -> InstanceProvider:
    if container is None:
        _ensure_constructible_without_container(cls)
    if scope is not Scope.SINGLETON:
        return InstanceProvider(cls, container, None)
    if container is None:
        return InstanceProvider(cls, None, cls())
    if isinstance(container, Resolver):
        return InstanceProvider(cls, container, container.resolve(cls))
    raise ControllerConfigError(
        f"{cls.__qualname__}: singleton controllers need a container with resolve()"
    )


def _ensure_constructible_without_container(cls: type[Controller]) -> None:
    required = [
        parameter.name
        for parameter in signature(cls).parameters.values()
        if parameter.default is parameter.empty
        and parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
    ]
    if required:
        raise ControllerConfigError(
            f"{cls.__qualname__}.__init__ requires {', '.join(map(repr, required))}; "
            "pass container=... to as_router()"
        )
