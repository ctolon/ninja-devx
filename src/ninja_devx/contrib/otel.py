"""OpenTelemetry tracing (one span per operation call) and HTTP server metrics.

::

    options = ControllerOptions(hooks=[OpenTelemetryHook()])
    use_middleware(api, OpenTelemetryMetricsMiddleware())
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

from django.http import HttpRequest, HttpResponseBase
from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode, Tracer

from ..http.middleware import Middleware
from ..routing.hooks import OperationInfo, get_operation

__all__ = ["OpenTelemetryHook", "OpenTelemetryMetricsMiddleware"]


@dataclass(frozen=True, slots=True)
class OpenTelemetryHook:
    tracer: Tracer | None = None
    """Tracer starting one span per call (default: the global tracer)."""

    @contextmanager
    def around(self, request: HttpRequest, operation: OperationInfo, /) -> Generator[None]:
        tracer = self.tracer or trace.get_tracer("ninja_devx")
        attributes = {
            "http.request.method": request.method or "",
            "url.path": request.path,
            "ninja_devx.operation_id": operation.operation_id,
            "code.function": operation.qualname,
        }
        with tracer.start_as_current_span(
            f"{request.method} {operation.path}", attributes=attributes, record_exception=False
        ) as span:
            try:
                yield
            except BaseException as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise


class OpenTelemetryMetricsMiddleware(Middleware):
    """HTTP server metrics per operation (OpenTelemetry semantic conventions).

    ``http.server.request.duration`` (histogram, seconds) and ``http.server.active_requests``
    (up-down counter), with ``http.request.method``, ``http.route`` and
    ``http.response.status_code``. Install on an API or router::

        use_middleware(api, OpenTelemetryMetricsMiddleware())
    """

    def __init__(self, meter: Meter | None = None) -> None:
        self._state_key = f"_ninja_devx_otel_{id(self)}"
        self.meter = meter or metrics.get_meter("ninja_devx")
        self.duration = self.meter.create_histogram(
            "http.server.request.duration",
            unit="s",
            description="Duration of HTTP server requests.",
        )
        self.active = self.meter.create_up_down_counter(
            "http.server.active_requests", unit="{request}", description="Requests in flight."
        )

    def process_request(self, request: HttpRequest) -> None:
        started = cast("list[float]", request.__dict__.setdefault(self._state_key, []))
        started.append(time.perf_counter())
        self.active.add(1, {"http.request.method": self._method(request)})
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        self._finish(request, {"http.response.status_code": response.status_code})
        return response

    def process_exception(self, request: HttpRequest, exception: BaseException) -> None:
        self._finish(request, {"error.type": type(exception).__name__})

    @staticmethod
    def _method(request: HttpRequest) -> str:
        method = request.method or ""
        return (
            method
            if method
            in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
            else "_OTHER"
        )

    def _finish(self, request: HttpRequest, outcome: dict[str, str | int]) -> None:
        starts = cast("list[float]", request.__dict__.get(self._state_key, []))
        if not starts:
            return
        started = starts.pop()
        if not starts:
            request.__dict__.pop(self._state_key, None)
        method = self._method(request)
        self.active.add(-1, {"http.request.method": method})
        match = getattr(request, "resolver_match", None)
        route: object = getattr(match, "route", None)
        operation = get_operation(request)
        template = (
            route if isinstance(route, str) else operation.path if operation else "<unmatched>"
        )
        self.duration.record(
            time.perf_counter() - started,
            {
                "http.request.method": method,
                "http.route": template,
                **outcome,
            },
        )
