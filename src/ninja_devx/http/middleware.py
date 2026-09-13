"""Middleware for Ninja routers and APIs, including plain function views.

Built on Ninja's ``add_decorator(..., mode="view")``: a middleware wraps the whole
operation (authentication, throttling, validation and the view), so it also sees the
401/422/429 responses::

    use_middleware(api, RequestIDMiddleware(), ServerTimingMiddleware())   # every operation
    use_middleware(router, DeprecationMiddleware(sunset=datetime(2027, 1, 1, tzinfo=UTC)))
    ControllerOptions(middleware=[RateLimitHeadersMiddleware()])            # one controller
    mount(api, V1, prefix="/v1", middleware=[DeprecationMiddleware(...)])   # a version

Write your own by subclassing ``Middleware`` and overriding ``process_request`` (return a
response to short-circuit) and/or ``process_response``. Override the ``a``-prefixed
methods too when the work is I/O in async operations.
"""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Final, cast

from django.http import HttpRequest, HttpResponseBase
from ninja import NinjaAPI, Router

__all__ = [
    "DeprecationMiddleware",
    "Middleware",
    "RateLimitHeadersMiddleware",
    "RequestIDMiddleware",
    "ServerTimingMiddleware",
    "get_request_id",
    "middleware_decorator",
    "record_rate_limit",
    "use_middleware",
]

Run = Callable[..., object]
_RATE_LIMITS_ATTR: Final = "_ninja_devx_rate_limits"
_REQUEST_ID_ATTR: Final = "_ninja_devx_request_id"


def get_request_id(request: HttpRequest) -> str | None:
    """The id ``RequestIDMiddleware`` accepted or created for ``request``.

    :param request: The current request.
    """
    request_id: str | None = request.__dict__.get(_REQUEST_ID_ATTR)
    return request_id


class Middleware:
    """Hooks around an operation. Both methods are optional."""

    def process_request(self, request: HttpRequest) -> HttpResponseBase | None:
        """Runs first; return a response to skip the operation."""
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """Runs last, on every response (including errors)."""
        return response

    async def aprocess_request(self, request: HttpRequest) -> HttpResponseBase | None:
        return self.process_request(request)

    async def aprocess_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        return self.process_response(request, response)

    def process_exception(self, request: HttpRequest, exception: BaseException) -> None:
        """Release per-request resources when request, handler or response processing fails.

        This is a cleanup hook; the original exception (including cancellation) is re-raised.
        """

    async def aprocess_exception(self, request: HttpRequest, exception: BaseException) -> None:
        self.process_exception(request, exception)


def middleware_decorator(*middlewares: Middleware) -> Callable[[Run], Run]:
    """A Ninja ``mode="view"`` decorator running ``middlewares`` (first is outermost).

    :param middlewares: Middleware instances; the first one is outermost.
    """

    def decorate(run: Run) -> Run:
        if inspect.iscoroutinefunction(run):
            call = cast("Callable[..., Awaitable[HttpResponseBase]]", run)

            async def arun(
                request: HttpRequest, *args: object, **kwargs: object
            ) -> HttpResponseBase:
                entered: list[Middleware] = []
                response: HttpResponseBase | None = None
                try:
                    for middleware in middlewares:
                        entered.append(middleware)
                        response = await middleware.aprocess_request(request)
                        if response is not None:
                            break
                    if response is None:
                        response = await call(request, *args, **kwargs)
                    while entered:
                        response = await entered[-1].aprocess_response(request, response)
                        entered.pop()
                    return response
                except BaseException as exc:
                    for middleware in reversed(entered):
                        try:
                            await middleware.aprocess_exception(request, exc)
                        except BaseException:
                            logging.getLogger(__name__).exception("Async middleware cleanup failed")
                    raise

            return arun

        sync_call = cast("Callable[..., HttpResponseBase]", run)

        def srun(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponseBase:
            entered: list[Middleware] = []
            response: HttpResponseBase | None = None
            try:
                for middleware in middlewares:
                    entered.append(middleware)
                    response = middleware.process_request(request)
                    if response is not None:
                        break
                if response is None:
                    response = sync_call(request, *args, **kwargs)
                while entered:
                    response = entered[-1].process_response(request, response)
                    entered.pop()
                return response
            except BaseException as exc:
                for middleware in reversed(entered):
                    try:
                        middleware.process_exception(request, exc)
                    except BaseException:
                        logging.getLogger(__name__).exception("Middleware cleanup failed")
                raise

        return srun

    return decorate


def use_middleware(target: NinjaAPI | Router, *middlewares: Middleware) -> None:
    """Apply ``middlewares`` to every operation of an API or router (call before mounting).

    :param target: A ``NinjaAPI`` or a ``Router``.
    :param middlewares: Middleware instances; the first one is outermost.
    """
    add_decorator: Callable[..., None] = getattr(target, "add_decorator")  # noqa: B009
    add_decorator(middleware_decorator(*middlewares), mode="view")


# --- Built-ins ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequestIDMiddleware(Middleware):
    """Accept or create a request id, expose it to the request and echo it in the response."""

    header: str = "X-Request-ID"
    """Header read from the request and written to the response."""
    generate: Callable[[], str] = field(default=lambda: uuid.uuid4().hex)
    """Makes an id when the client sends none."""

    def process_request(self, request: HttpRequest) -> None:
        meta_key = "HTTP_" + self.header.upper().replace("-", "_")
        if not request.headers.get(self.header):
            request.META[meta_key] = self.generate()
            vars(request).pop("headers", None)  # HttpHeaders is cached on first access
        request.__dict__[_REQUEST_ID_ATTR] = request.headers.get(self.header)
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        request_id = request.headers.get(self.header)
        if request_id and not response.has_header(self.header):
            response[self.header] = request_id
        return response


class ServerTimingMiddleware(Middleware):
    """``Server-Timing: app;dur=<ms>`` for browser dev tools and APM."""

    _STARTED: Final = "_ninja_devx_started"

    def process_request(self, request: HttpRequest) -> None:
        request.__dict__[self._STARTED] = time.perf_counter()
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        started: object = request.__dict__.get(self._STARTED)
        if isinstance(started, float):
            elapsed = (time.perf_counter() - started) * 1000
            response["Server-Timing"] = f"app;dur={elapsed:.1f}"
        return response


@dataclass(frozen=True, slots=True)
class DeprecationMiddleware(Middleware):
    """``Deprecation`` (RFC 9745), ``Sunset`` (RFC 8594) and ``Link`` headers for old versions."""

    deprecated_at: datetime | None = None
    """When the API was deprecated; ``None`` sends ``Deprecation: true``."""
    sunset: datetime | None = None
    """When it stops working."""
    link: str | None = None
    """Migration guide URL (``Link: <...>; rel="deprecation"``)."""

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        if self.deprecated_at is not None:
            response["Deprecation"] = f"@{int(self.deprecated_at.timestamp())}"
        else:
            response["Deprecation"] = "true"
        if self.sunset is not None:
            response["Sunset"] = format_datetime(self.sunset.astimezone(UTC), usegmt=True)
        if self.link:
            response["Link"] = f'<{self.link}>; rel="deprecation"'
        return response


@dataclass(frozen=True, slots=True)
class _RateLimit:
    policy: str
    limit: int
    remaining: int
    reset: float
    window: int


def record_rate_limit(
    request: HttpRequest, *, policy: str, limit: int, remaining: int, reset: float, window: int
) -> None:
    """Remember a throttle's state for ``RateLimitHeadersMiddleware`` (throttles call it).

    :param request: The current request.
    :param policy: Throttle name, shown in ``RateLimit-Policy``.
    :param limit: Requests allowed per window.
    :param remaining: Requests left in the current window.
    :param reset: Seconds until the window resets.
    :param window: Window length in seconds.
    """
    found: list[_RateLimit] = request.__dict__.setdefault(_RATE_LIMITS_ATTR, [])
    found.append(_RateLimit(policy, limit, max(remaining, 0), reset, window))


class RateLimitHeadersMiddleware(Middleware):
    """``RateLimit-Limit/Remaining/Reset`` and ``RateLimit-Policy`` from ninja-devx throttles.

    The most restrictive throttle of the request is reported. Added automatically to
    controllers whose ``throttle`` uses ``ninja_devx.http.throttling`` classes.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        limits: Sequence[_RateLimit] = request.__dict__.get(_RATE_LIMITS_ATTR, ())
        if not limits:
            return response
        tightest = min(limits, key=lambda item: (item.remaining, item.reset))
        response["RateLimit-Limit"] = str(tightest.limit)
        response["RateLimit-Remaining"] = str(tightest.remaining)
        response["RateLimit-Reset"] = str(max(int(tightest.reset + 0.999), 0))
        response["RateLimit-Policy"] = ", ".join(
            f'"{item.policy}";q={item.limit};w={item.window}' for item in limits
        )
        return response
