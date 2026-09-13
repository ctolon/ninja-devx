"""Write a method once, call it from sync and async code.

::

    class OrderService:
        @dual
        def place(self, command: PlaceOrder) -> Order: ...

    service.place(command)            # sync
    await service.place.a(command)    # async: one thread hop, or the native implementation

    @place.native
    async def _place_async(self, command: PlaceOrder) -> Order: ...

Django's ORM is synchronous underneath, so the default ``.a`` runs the sync body in a
single ``sync_to_async(thread_sensitive=True)`` hop. Provide ``native`` only when the
async version really avoids blocking (HTTP calls, ``asyncio`` libraries...).
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Concatenate, Generic, ParamSpec, TypeVar, cast, overload

from asgiref.sync import sync_to_async

__all__ = ["BoundDual", "dual"]

S = TypeVar("S")
P = ParamSpec("P")
R = TypeVar("R")


class dual(Generic[S, P, R]):
    """A method with a sync implementation and an optional native async one."""

    __slots__ = ("_async", "_sync", "name")

    def __init__(self, function: Callable[Concatenate[S, P], R]) -> None:
        self._sync = function
        self._async: Callable[Concatenate[S, P], Awaitable[R]] | None = None
        self.name = function.__name__

    def __set_name__(self, owner: type[S], name: str) -> None:
        self.name = name

    def native(
        self, function: Callable[Concatenate[S, P], Awaitable[R]]
    ) -> Callable[Concatenate[S, P], Awaitable[R]]:
        """Register the native async implementation used by ``.a``."""
        self._async = function
        return function

    @property
    def has_native(self) -> bool:
        return self._async is not None

    @overload
    def __get__(self, instance: None, owner: type[S]) -> dual[S, P, R]: ...

    @overload
    def __get__(self, instance: S, owner: type[S]) -> BoundDual[P, R]: ...

    def __get__(self, instance: S | None, owner: type[S]) -> dual[S, P, R] | BoundDual[P, R]:
        if instance is None:
            return self
        sync = cast("Callable[P, R]", functools.partial(self._sync, instance))
        native = self._async
        bound_native = (
            None
            if native is None
            else cast("Callable[P, Awaitable[R]]", functools.partial(native, instance))
        )
        return BoundDual(sync, bound_native)


class BoundDual(Generic[P, R]):
    """``dual`` bound to an instance: call it, or await ``.a(...)``."""

    __slots__ = ("_native", "_sync")

    def __init__(self, sync: Callable[P, R], native: Callable[P, Awaitable[R]] | None) -> None:
        self._sync = sync
        self._native = native

    @property
    def has_native(self) -> bool:
        return self._native is not None

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self._sync(*args, **kwargs)

    async def a(self, *args: P.args, **kwargs: P.kwargs) -> R:
        if self._native is not None:
            return await self._native(*args, **kwargs)
        run: Callable[P, Awaitable[R]] = sync_to_async(self._sync, thread_sensitive=True)
        return await run(*args, **kwargs)
