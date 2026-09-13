"""Operation metadata and hooks around every operation call."""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from contextlib import AbstractAsyncContextManager, AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, TypeVar, runtime_checkable

from django.http import HttpRequest

if TYPE_CHECKING:
    from .controller import Controller

__all__ = [
    "AsyncOperationHook",
    "LoggingHook",
    "OperationHook",
    "OperationInfo",
    "get_operation",
]

MetaT = TypeVar("MetaT")

_REQUEST_ATTR: Final = "_ninja_devx_operation"


@dataclass(frozen=True, slots=True)
class OperationInfo:
    """Static description of an operation, built once per registration."""

    controller: type[Controller]
    """The controller class."""
    method_name: str
    """The Python method implementing the operation."""
    operation_id: str
    """The OpenAPI operation id."""
    http_methods: tuple[str, ...]
    """HTTP methods of the operation."""
    path: str
    """The path as declared on the router."""
    is_async: bool
    """Whether the registered implementation is async."""
    metadata: tuple[object, ...] = ()
    """Typed metadata from ``meta=`` (controller and operation), for permissions and hooks."""
    database: str | None = None
    """Explicit operation/controller database alias, if configured."""

    @property
    def qualname(self) -> str:
        return f"{self.controller.__qualname__}.{self.method_name}"

    def meta(self, kind: type[MetaT]) -> MetaT | None:
        """The closest metadata item of ``kind``: ``operation.meta(RequiresScope)``."""
        for item in reversed(self.metadata):
            if isinstance(item, kind):
                return item
        return None


def bind_operation(request: HttpRequest, operation: OperationInfo) -> None:
    setattr(request, _REQUEST_ATTR, operation)


def get_operation(request: HttpRequest) -> OperationInfo | None:
    """The operation handling ``request``, available to permissions, services and hooks."""
    operation: OperationInfo | None = getattr(request, _REQUEST_ATTR, None)
    return operation


@runtime_checkable
class OperationHook(Protocol):
    """Wraps each call of an operation, including permission checks.

    Exceptions (denials, 404s, errors) propagate through the context manager, so
    hooks can observe them. Hooks are shared between requests and must not store
    per-request state on ``self``.
    """

    def around(
        self, request: HttpRequest, operation: OperationInfo, /
    ) -> AbstractContextManager[None]: ...


@runtime_checkable
class AsyncOperationHook(Protocol):
    """A hook with an async context manager, used by async operations.

    A hook may implement both ``around`` and ``around_async``; async operations prefer
    ``around_async``. Sync-only hooks also run in async operations and are assumed not to
    block; set ``blocking = True`` on them to run their enter/exit in a thread.
    """

    def around_async(
        self, request: HttpRequest, operation: OperationInfo, /
    ) -> AbstractAsyncContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class LoggingHook:
    """Logs one structured record per operation call with its duration and outcome."""

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("ninja_devx"))
    """Logger receiving one record per call."""
    level: int = logging.INFO
    """Log level of the record."""

    @contextmanager
    def around(self, request: HttpRequest, operation: OperationInfo, /) -> Generator[None]:
        started = time.perf_counter()
        outcome = "ok"
        try:
            yield
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            self.logger.log(
                self.level,
                "%s %s -> %s (%.3f ms)",
                request.method,
                operation.operation_id,
                outcome,
                duration_ms,
                extra={
                    "operation_id": operation.operation_id,
                    "controller": operation.qualname,
                    "http_method": request.method,
                    "path": request.path,
                    "outcome": outcome,
                    "duration_ms": duration_ms,
                },
            )
