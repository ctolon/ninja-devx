"""Acquire after controller preflight; persist after Ninja serializes the response."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, JsonResponse
from django.http.response import HttpResponseBase
from django.utils.translation import gettext as _
from ninja.decorators import decorate_view

from .._internal.types import ViewDecorator, ViewFunction
from ..configuration.settings import get_settings
from ..security.auth import request_user
from ..security.tenancy import current_tenant
from .store import Claim, acquire

RunFunction = Callable[..., HttpResponseBase | Awaitable[HttpResponseBase]]


def _identity(value: object) -> str:
    if value is None:
        return "anonymous"
    if isinstance(value, str | int | UUID):
        return f"{type(value).__name__}:{value}"
    pk: object = getattr(value, "pk", None)
    if isinstance(pk, str | int | UUID):
        cls = type(value)
        return f"{cls.__module__}.{cls.__qualname__}:{pk}"
    raise ImproperlyConfigured(
        "idempotent() needs scope= for a custom authentication or tenant identity"
    )


@dataclass(frozen=True, slots=True)
class Policy:
    header: str
    ttl: int | None
    database: str | None
    required: bool
    scope: Callable[[HttpRequest], str] | None

    def before(self, request: HttpRequest) -> HttpResponseBase | None:
        key = request.headers.get(self.header)
        if not key:
            return (
                JsonResponse(
                    {"detail": _("The %(header)s header is required") % {"header": self.header}},
                    status=400,
                )
                if self.required
                else None
            )
        if len(key) > 255:
            return JsonResponse({"detail": _("Idempotency key exceeds 255 characters")}, status=400)
        user = request_user(request)
        principal = user if user is not None else getattr(request, "auth", None)
        tenant = current_tenant(request)
        if tenant is None:
            tenant = getattr(request, "tenant", None)
        # scope= is responsible for custom principal AND tenant identity; credentials
        # still partition keys so rotating/restricting a token cannot reveal its replay.
        identity = self.scope(request) if self.scope else [_identity(principal), _identity(tenant)]
        # APIKeyAuth subclasses may rename their header. Use the validated credential
        # as well, so header spelling and secret rotation cannot merge replay scopes.
        api_key: object = request.__dict__.get("_ninja_devx_api_key")
        parts = [
            request.method,
            request.path,
            identity,
            key,
            request.headers.get("Authorization", ""),
            request.headers.get("X-API-Key", ""),
            request.COOKIES.get(settings.SESSION_COOKIE_NAME, ""),
            _identity(api_key),
            getattr(api_key, "hashed_secret", ""),
        ]
        digest = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    request.get_full_path(),
                    request.headers.get("Content-Type", ""),
                    request.headers.get("Accept", ""),
                    request.headers.get("Accept-Language", ""),
                ]
            ).encode()
            + b"\0"
            + request.body
        ).hexdigest()
        ttl = self.ttl if self.ttl is not None else get_settings().idempotency_ttl
        if ttl <= 0:
            raise ImproperlyConfigured("IDEMPOTENCY_TTL must be positive")
        outcome = acquire(
            digest,
            fingerprint,
            database=self.database or get_settings().idempotency_database,
            ttl=ttl,
        )
        if isinstance(outcome, Claim):
            request.__dict__["_ninja_devx_idempotency_claim"] = outcome
            return None
        return outcome

    def __call__(self, view: ViewFunction) -> ViewFunction:
        if not view.__dict__.get("_ninja_devx_idempotency_ready"):
            raise ImproperlyConfigured("Use idempotent() in a controller's decorators= option")

        def finish(request: HttpRequest, response: HttpResponseBase) -> None:
            claim = request.__dict__.pop("_ninja_devx_idempotency_claim", None)
            if isinstance(claim, Claim):
                claim.finish(response)

        def decorate(run: RunFunction) -> RunFunction:
            if inspect.iscoroutinefunction(run):

                async def async_run(request: HttpRequest, **kwargs: object) -> HttpResponseBase:
                    result = run(request, **kwargs)
                    response: HttpResponseBase = (
                        await result if inspect.isawaitable(result) else result
                    )
                    await sync_to_async(finish)(request, response)
                    return response

                return async_run

            def sync_run(request: HttpRequest, **kwargs: object) -> HttpResponseBase:
                response = run(request, **kwargs)
                assert isinstance(response, HttpResponseBase)
                finish(request, response)
                return response

            return sync_run

        decorated: ViewFunction = decorate_view(decorate)(view)
        return decorated


def idempotent(
    *,
    header: str = "Idempotency-Key",
    ttl: int | None = None,
    database: str | None = None,
    required: bool = False,
    scope: Callable[[HttpRequest], str] | None = None,
) -> ViewDecorator:
    """Replay completed responses after authentication, permissions and bindings.

    Install ``ninja_devx`` and run its migrations. Unfinished requests remain claimed
    until explicitly reconciled; they never expire or repeat automatically. Completed
    responses (including errors) are replayed for ``ttl`` seconds. Custom authorization
    belongs in bindings, ``before_operation`` or ``authorize_replay``, which run on retries.

    :param header: Request header carrying a key of at most 255 characters.
    :param ttl: Completed response retention; defaults to ``IDEMPOTENCY_TTL``.
    :param database: Durable database alias; defaults to ``IDEMPOTENCY_DATABASE``.
    :param required: Reject a missing key with HTTP 400.
    :param scope: Stable custom principal and tenant identity, if not model/scalar values.
    """
    if ttl is not None and ttl <= 0:
        raise ValueError("idempotency ttl must be positive")
    if not header or not header.isascii() or any(c.isspace() or c == ":" for c in header):
        raise ValueError("idempotency header must be a valid HTTP field name")
    return Policy(header, ttl, database, required, scope)
