"""One structured log record per request: identity, timing and outcome, never the body.

::

    install(api, [RequestLogPlugin()])

    LOGGING = {
        "version": 1,
        "formatters": {"json": {"()": "ninja_devx.http.requestlog.JSONFormatter"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
        "loggers": {"ninja_devx.request": {"handlers": ["console"], "level": "INFO"}},
    }

Fields: ``request_id``, ``method``, ``path``, ``status``, ``duration_ms``, ``user_id``,
``tenant`` (the resolved tenant, or ``request.tenant`` as tenant middleware sets it; ``None``
without one), ``query_count`` (queries on the default database connection), ``operation``
(``"Controller.method"``), and ``api_key_prefix`` when ``ninja_devx.contrib.apikeys``
authenticated the request. ``naming="otel"`` (the default) spells the four fields with an
OpenTelemetry semantic convention name (``http.request.method``, ``url.path``,
``http.response.status_code``, ``user.id``); ``naming="flat"`` keeps the plain names above.

When `structlog <https://www.structlog.org/>`_ is installed, the identity fields
(``request_id``, method, path, user id, tenant) are also bound with
``structlog.contextvars.bind_contextvars`` as soon as the request starts and cleared once
the final record is logged, so the application's own ``structlog`` calls carry them too;
fields that are only known once the handler returns (status, duration, query count,
operation) are not part of that binding.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from json import dumps
from types import MappingProxyType
from typing import Final, Literal, Protocol, TypeAlias, cast

from django.db import connection
from django.http import HttpRequest, HttpResponseBase

from ..plugins import APIPlugin
from ..routing.hooks import get_operation
from ..security.auth import request_user
from ..security.tenancy import current_tenant
from .middleware import Middleware, RequestIDMiddleware, get_request_id

__all__ = ["JSONFormatter", "RequestLogMiddleware", "RequestLogPlugin", "register_api_key_reader"]

Naming = Literal["otel", "flat"]

_STATE_ATTR: Final = "_ninja_devx_request_log"
_OTEL_NAMES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "method": "http.request.method",
        "path": "url.path",
        "status": "http.response.status_code",
        "user_id": "user.id",
    }
)
_RESERVED: Final = frozenset(vars(logging.makeLogRecord({})))


@contextmanager
def _count_queries() -> Generator[Callable[[], int]]:
    count = 0

    def wrapper(
        execute: Callable[..., object], sql: str, params: object, many: bool, context: object
    ) -> object:
        nonlocal count
        count += 1
        return execute(sql, params, many, context)

    with connection.execute_wrapper(wrapper):
        yield lambda: count


ApiKeyReader: TypeAlias = Callable[[HttpRequest], "str | None"]
_api_key_reader: ApiKeyReader | None = None


def register_api_key_reader(reader: ApiKeyReader) -> None:
    """Let ``ninja_devx.contrib.apikeys`` (or your own auth) contribute ``api_key_prefix``.

    Apps register in ``AppConfig.ready``; core never imports contrib.

    :param reader: Returns the authenticating key's prefix, or ``None``.
    """
    global _api_key_reader
    _api_key_reader = reader


def _api_key_prefix(request: HttpRequest) -> str | None:
    return _api_key_reader(request) if _api_key_reader is not None else None


class _StructlogContextvars(Protocol):
    def bind_contextvars(self, **kwargs: object) -> object: ...

    def unbind_contextvars(self, *keys: str) -> object: ...


class _Structlog(Protocol):
    contextvars: _StructlogContextvars


def _structlog() -> _Structlog | None:
    try:
        module = importlib.import_module("structlog")
    except ImportError:
        return None
    return cast("_Structlog", module)


def _tenant_id(request: HttpRequest) -> object | None:
    tenant = current_tenant(request) or getattr(request, "tenant", None)
    return None if tenant is None else getattr(tenant, "pk", tenant)


@dataclass(slots=True)
class _State:
    started: float
    identity: Mapping[str, object]
    queries: Callable[[], int]
    stop_counting: Callable[[], None]


@dataclass(frozen=True, slots=True)
class RequestLogMiddleware(Middleware):
    """Logs one record per request. Install directly, or through ``RequestLogPlugin``."""

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("ninja_devx.request"))
    """Logger receiving one record per request."""
    level: int = logging.INFO
    """Log level of the record."""
    naming: Naming = "otel"
    """``"otel"``: OpenTelemetry semantic convention field names; ``"flat"``: plain ones."""
    bind_structlog: bool = True
    """Bind the identity fields to ``structlog.contextvars`` when structlog is installed."""

    def _name(self, key: str) -> str:
        return _OTEL_NAMES[key] if self.naming == "otel" and key in _OTEL_NAMES else key

    def _identity(self, request: HttpRequest) -> dict[str, object]:
        user = request_user(request)
        return {
            "request_id": get_request_id(request),
            self._name("method"): request.method,
            self._name("path"): request.path,
            self._name("user_id"): getattr(user, "pk", None),
            "tenant": _tenant_id(request),
        }

    def process_request(self, request: HttpRequest) -> None:
        counter = _count_queries()
        queries = counter.__enter__()

        def stop_counting() -> None:
            counter.__exit__(None, None, None)

        identity = self._identity(request)
        request.__dict__[_STATE_ATTR] = _State(
            time.perf_counter(), identity, queries, stop_counting
        )
        structlog = _structlog() if self.bind_structlog else None
        if structlog is not None:
            structlog.contextvars.bind_contextvars(**identity)
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        self._finish(request, status=response.status_code)
        return response

    def process_exception(self, request: HttpRequest, exception: BaseException) -> None:
        self._finish(request, status=None, error=type(exception).__name__)

    def _finish(
        self, request: HttpRequest, *, status: int | None, error: str | None = None
    ) -> None:
        state = cast("_State | None", request.__dict__.pop(_STATE_ATTR, None))
        if state is None:
            return
        state.stop_counting()
        duration_ms = round((time.perf_counter() - state.started) * 1000, 3)
        operation = get_operation(request)
        fields: dict[str, object] = {
            **self._identity(request),
            self._name("status"): status,
            "duration_ms": duration_ms,
            "query_count": state.queries(),
            "operation": operation.qualname if operation is not None else None,
            "api_key_prefix": _api_key_prefix(request),
        }
        if error is not None:
            fields["error"] = error
        self.logger.log(
            self.level,
            "%s %s -> %s (%.3fms)",
            request.method,
            request.path,
            status if status is not None else error,
            duration_ms,
            extra=fields,
        )
        structlog = _structlog() if self.bind_structlog else None
        if structlog is not None:
            structlog.contextvars.unbind_contextvars(*state.identity)


@dataclass(frozen=True, slots=True)
class RequestLogPlugin(APIPlugin):
    """``RequestLogMiddleware`` plus ``RequestIDMiddleware``, so every request gets an id.

    ::

        install(api, [RequestLogPlugin(naming="flat")])
    """

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("ninja_devx.request"))
    """Logger receiving one record per request."""
    level: int = logging.INFO
    """Log level of the record."""
    naming: Naming = "otel"
    """``"otel"``: OpenTelemetry semantic convention field names; ``"flat"``: plain ones."""
    bind_structlog: bool = True
    """Bind the identity fields to ``structlog.contextvars`` when structlog is installed."""
    include_request_id: bool = True
    """Add ``RequestIDMiddleware`` too, so ``request_id`` is never empty."""

    def middleware(self) -> tuple[Middleware, ...]:
        logging_middleware = RequestLogMiddleware(
            logger=self.logger,
            level=self.level,
            naming=self.naming,
            bind_structlog=self.bind_structlog,
        )
        if not self.include_request_id:
            return (logging_middleware,)
        return (RequestIDMiddleware(), logging_middleware)


class JSONFormatter(logging.Formatter):
    """Render each ``LogRecord`` as one JSON object, ``extra`` fields included.

    ::

        "formatters": {"json": {"()": "ninja_devx.http.requestlog.JSONFormatter"}}
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return dumps(payload, default=str)
