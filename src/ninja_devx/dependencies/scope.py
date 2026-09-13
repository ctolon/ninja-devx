"""Synchronous and asynchronous dependency scope lifecycle."""

from __future__ import annotations

from types import TracebackType
from typing import (
    TYPE_CHECKING,
    cast,
)

from .contracts import Factory, T
from .engine import ResolutionEngine
from .state import ScopeState

if TYPE_CHECKING:
    from .container import Container


class Scope:
    """A resolution scope: ``with container.scope() as scope: scope.resolve(Service)``."""

    __slots__ = ("_container", "_state")

    def __init__(self, container: Container, state: ScopeState) -> None:
        self._container = container
        self._state = state

    def resolve(self, key: Factory[T], /) -> T:
        return cast(T, ResolutionEngine(self._container, self._state).resolve(key, ()))

    async def aresolve(self, key: Factory[T], /) -> T:
        return cast(T, await ResolutionEngine(self._container, self._state).aresolve(key, ()))

    def __enter__(self) -> Scope:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self._state.exits.__exit__(exc_type, exc_value, traceback)
        finally:
            self._state.instances.clear()

    async def __aenter__(self) -> Scope:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self._state.async_exits.__aexit__(exc_type, exc_value, traceback)
            self._state.exits.__exit__(exc_type, exc_value, traceback)
        finally:
            self._state.instances.clear()
