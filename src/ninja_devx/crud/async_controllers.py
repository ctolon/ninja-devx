"""Async CRUD controllers: the same classes as the sync ones, with ``mode = "async"``.

Every CRUD mixin carries both implementations (see ``async_variant``); these aliases
just pick the async ones, so routes, operation ids and hooks are identical.
"""

from __future__ import annotations

from typing import ClassVar, Generic, Literal

from .controllers import CRUDController, InT, ModelT, OutT, ReadOnlyModelController

__all__ = ["AsyncCRUDController", "AsyncReadOnlyModelController"]


class AsyncReadOnlyModelController(ReadOnlyModelController[ModelT, OutT], Generic[ModelT, OutT]):
    mode: ClassVar[Literal["sync", "async", "auto"]] = "async"


class AsyncCRUDController(CRUDController[ModelT, OutT, InT], Generic[ModelT, OutT, InT]):
    mode: ClassVar[Literal["sync", "async", "auto"]] = "async"
