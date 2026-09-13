"""Inject dependencies into operation parameters: ``service: Inject[OrderService]``.

::

    @post("/", response={201: OrderOut})
    async def place(self, request: HttpRequest, payload: OrderIn,
                    orders: Inject[OrderService]) -> Status[Order]: ...

    @get("/whoami")
    def whoami(
        self, request: HttpRequest, ip: Annotated[str, Resolve(client_ip)]
    ) -> dict[str, str]: ...

Injected parameters never appear in OpenAPI. ``Inject`` resolves from the controller's
container inside the request scope (checked when the router is built); ``Resolve`` calls a
function with the request.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Generic, TypeAlias, TypeVar, cast

from django.http import HttpRequest

from ..exceptions import ControllerConfigError
from ..routing.bindings import Arguments, BindingMarker, ParameterBinding
from .container import CheckableContainer, ContainerLike, Resolver

if TYPE_CHECKING:
    from ..routing.controller import Controller
    from ..routing.operations import OperationSpec
    from .instances import Invocation

__all__ = ["Inject", "Resolve", "resolve"]

T = TypeVar("T")


class _InjectMarker(BindingMarker):
    def bind(
        self,
        parameter: inspect.Parameter,
        annotation: object,
        controller: type[Controller],
        spec: OperationSpec,
    ) -> ParameterBinding:
        key = annotation
        qualname = f"{controller.__qualname__}.{parameter.name}"

        def check(container: ContainerLike | None, asynchronous: bool) -> None:
            if container is None:
                raise ControllerConfigError(
                    f"{qualname}: Inject[...] needs as_router(container=...)"
                )
            if isinstance(container, CheckableContainer):
                container.check(cast("type[object]", key), asynchronous=asynchronous)

        def resolve_value(invocation: Invocation, arguments: Arguments) -> object:
            return _sync_resolver(invocation, qualname).resolve(cast("type[object]", key))

        async def aresolve_value(invocation: Invocation, arguments: Arguments) -> object:
            if invocation.async_resolver is not None:
                return await invocation.async_resolver.aresolve(cast("type[object]", key))
            return resolve_value(invocation, arguments)

        return ParameterBinding(
            name=parameter.name,
            parameters=(),
            resolve=resolve_value,
            aresolve=aresolve_value,
            needs_resolver=True,
            check=check,
        )


def _sync_resolver(invocation: Invocation, qualname: str) -> Resolver:
    if invocation.resolver is None:
        raise ControllerConfigError(f"{qualname}: no container is available to inject from")
    return invocation.resolver


Inject: TypeAlias = Annotated[T, _InjectMarker()]
"""A parameter resolved from the controller's container (not part of the API)."""


class Resolve(BindingMarker, Generic[T]):
    """``Annotated[T, Resolve(fn)]``: the value of ``fn(request)`` (sync or async ``fn``)."""

    def __init__(
        self, function: Callable[[HttpRequest], T] | Callable[[HttpRequest], Awaitable[T]]
    ) -> None:
        self.function = function

    def bind(
        self,
        parameter: inspect.Parameter,
        annotation: object,
        controller: type[Controller],
        spec: OperationSpec,
    ) -> ParameterBinding:
        function = self.function
        is_async = inspect.iscoroutinefunction(function)
        qualname = f"{controller.__qualname__}.{parameter.name}"

        def check(container: ContainerLike | None, asynchronous: bool) -> None:
            if is_async and not asynchronous:
                raise ControllerConfigError(
                    f"{qualname}: Resolve({function.__name__}) is async; "
                    "use it in an async operation"
                )

        def resolve_value(invocation: Invocation, arguments: Arguments) -> object:
            return function(invocation.request)

        async def aresolve_value(invocation: Invocation, arguments: Arguments) -> object:
            value = function(invocation.request)
            return await value if inspect.isawaitable(value) else value

        return ParameterBinding(
            name=parameter.name,
            parameters=(),
            resolve=resolve_value,
            aresolve=aresolve_value,
            check=check,
        )


def resolve(request: HttpRequest, key: Callable[..., T]) -> T:
    """Resolve ``key`` from the current operation's DI scope (for code without parameters)."""
    from .instances import get_invocation

    invocation = get_invocation(request)
    if invocation is None or invocation.resolver is None:
        raise ControllerConfigError("resolve() needs an operation running with a container")
    resolver = cast("Callable[[Callable[..., T]], T]", getattr(invocation.resolver, "resolve"))  # noqa: B009
    return resolver(key)


def injected(key: object) -> object:
    """The runtime form of ``Inject[key]`` for dynamically built signatures."""
    marker: BindingMarker = _InjectMarker()
    return Annotated[key, marker]
