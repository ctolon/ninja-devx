"""Creating, authenticating and scoping API keys."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, cast

from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_noop
from ninja.security import APIKeyHeader, HttpBearer

from ...http.throttling import RateThrottle
from ...routing.hooks import get_operation
from ...security.permissions import BasePermission
from .models import APIKey, validate_rate

__all__ = [
    "KEEP",
    "APIKeyAuth",
    "APIKeyBearer",
    "APIKeyRateThrottle",
    "HasScopes",
    "RequiresScope",
    "create_api_key",
    "current_api_key",
    "revoke_api_key",
    "rotate_api_key",
    "scope_allows",
]

KEY_PREFIX: Final = "ndx"
_KEY_ATTR: Final = "_ninja_devx_api_key"
_TOUCH_EVERY: Final = timedelta(minutes=1)
KEEP: Final = object()
"""Sentinel: keep the current value when rotating a key."""


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_api_key(
    user: object,
    name: str,
    *,
    scopes: Iterable[str] = (),
    expires_at: datetime | None = None,
    rate_limit: str = "",
) -> tuple[APIKey, str]:
    """Create a key and return it with its raw value, which is never stored or shown again.

    :param user: The key's owner; requests authenticated with it act as this user.
    :param name: A label for humans (``"CI deploys"``).
    :param scopes: What the key may do (``"orders:read"``, ``"orders:*"``, ``"*"``).
    :param expires_at: When the key stops working (``None``: never).
    :param rate_limit: This key's rate for ``APIKeyRateThrottle`` (``"1000/hour"``; empty:
        the throttle's default).
    """
    if rate_limit:
        validate_rate(rate_limit)
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    key = APIKey.objects.create(
        user=user,
        name=name,
        prefix=prefix,
        hashed_secret=_digest(secret),
        scopes=sorted(set(scopes)),
        expires_at=expires_at,
        rate_limit=rate_limit,
    )
    return key, f"{KEY_PREFIX}_{prefix}_{secret}"


def revoke_api_key(key: APIKey) -> None:
    """Stop accepting ``key`` (kept for audit).

    :param key: The key to revoke.
    """
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])


def rotate_api_key(
    key: APIKey,
    *,
    scopes: Iterable[str] | None = None,
    expires_at: object = KEEP,
    rate_limit: object = KEEP,
) -> tuple[APIKey, str]:
    """Replace ``key``'s secret in place and return the new raw value.

    The old secret stops working immediately. The row (owner, name, creation time) is kept;
    pass ``scopes`` to update them, and ``expires_at``/``rate_limit`` (including ``None`` to
    clear them) to change the expiry or the limit. A revoked key stays revoked: create a
    new one instead.

    :param key: The key to rotate.
    :param scopes: New scopes, or ``None`` to keep the current ones.
    :param expires_at: New expiry; omit to keep the current one.
    :param rate_limit: New rate limit; omit to keep the current one.
    :raises ValueError: ``key`` is revoked.
    """
    if key.revoked_at is not None:
        raise ValueError("a revoked API key cannot be rotated")
    if rate_limit is not KEEP and rate_limit is not None:
        validate_rate(str(rate_limit))
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    key.prefix = prefix
    key.hashed_secret = _digest(secret)
    fields = ["prefix", "hashed_secret"]
    if scopes is not None:
        key.scopes = sorted(set(scopes))
        fields.append("scopes")
    if expires_at is not KEEP:
        key.expires_at = cast("datetime | None", expires_at)
        fields.append("expires_at")
    if rate_limit is not KEEP:
        key.rate_limit = "" if rate_limit is None else str(rate_limit)
        fields.append("rate_limit")
    key.save(update_fields=fields)
    return key, f"{KEY_PREFIX}_{prefix}_{secret}"


def _parse(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    return parts[1], parts[2]


def _valid(key: APIKey | None, secret: str) -> bool:
    if key is None or key.revoked_at is not None:
        return False
    expires: object = key.expires_at
    if isinstance(expires, datetime) and expires <= timezone.now():
        return False
    if not getattr(key.user, "is_active", False):
        return False
    return hmac.compare_digest(key.hashed_secret, _digest(secret))


def _needs_touch(key: APIKey) -> bool:
    last: object = key.last_used_at
    return not isinstance(last, datetime) or timezone.now() - last > _TOUCH_EVERY


def current_api_key(request: HttpRequest) -> APIKey | None:
    """The API key that authenticated ``request``, if any."""
    key: APIKey | None = request.__dict__.get(_KEY_ATTR)
    return key


def _authenticate(request: HttpRequest, raw: str | None) -> object | None:
    parsed = _parse(raw)
    if parsed is None:
        return None
    prefix, secret = parsed
    key = APIKey.objects.select_related("user").filter(prefix=prefix).first()
    if not _valid(key, secret) or key is None:
        return None
    if _needs_touch(key):
        APIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
    request.__dict__[_KEY_ATTR] = key
    return key.user


class APIKeyAuth(APIKeyHeader):
    """``X-API-Key: ndx_...``; ``request.auth`` is the key's user.

    In async operations Ninja runs it in one thread hop (lookup and ``last_used_at``
    together), which is cheaper than two async ORM calls.
    """

    param_name = "X-API-Key"

    def authenticate(self, request: HttpRequest, key: str | None) -> object | None:
        return _authenticate(request, key)


class APIKeyBearer(HttpBearer):
    """``Authorization: Bearer ndx_...``."""

    def authenticate(self, request: HttpRequest, token: str) -> object | None:
        return _authenticate(request, token)


@dataclass(frozen=True, slots=True)
class RequiresScope:
    """Operation metadata: ``meta=(RequiresScope("orders:write"),)``."""

    scope: str


def scope_allows(granted: Sequence[str], required: str) -> bool:
    """Whether ``granted`` covers ``required``: exact, ``"orders:*"`` or ``"*"``.

    :param granted: Scopes of the key.
    :param required: The scope an operation needs (``"orders:write"``).
    """
    resource = required.split(":", 1)[0]
    return any(scope in {required, "*", f"{resource}:*"} for scope in granted)


@dataclass(frozen=True, slots=True)
class HasScopes(BasePermission[object]):
    """Every ``RequiresScope`` of the operation must be granted to the request's API key.

    Requests authenticated otherwise (sessions, JWT) pass when ``allow_unscoped`` is set,
    since their scopes are the user's permissions.
    """

    allow_unscoped: bool = False
    """Let requests without an API key through (e.g. session users of the same API)."""

    message = gettext_noop("The API key does not have the required scope.")

    def has_permission(self, request: HttpRequest, /) -> bool:
        operation = get_operation(request)
        metadata = operation.metadata if operation else ()
        required = [item.scope for item in metadata if isinstance(item, RequiresScope)]
        key = current_api_key(request)
        if key is None:
            return self.allow_unscoped or not required
        granted: object = key.scopes
        items: list[object] = cast("list[object]", granted) if isinstance(granted, list) else []
        scopes = [str(scope) for scope in items]
        return all(scope_allows(scopes, scope) for scope in required)

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        return self.has_permission(request)  # reads what authentication already loaded


class APIKeyRateThrottle(RateThrottle):
    """Per API key: the key's own ``rate_limit``, else ``rate``; other requests pass.

    ``ControllerOptions(throttle=[APIKeyRateThrottle("1000/hour"), UserRateThrottle(...)])``
    """

    scope = "apikey"

    def identify(self, request: HttpRequest) -> str | None:
        key = current_api_key(request)
        return None if key is None else f"apikey:{key.pk}"

    def get_rate(self, request: HttpRequest) -> str | None:
        key = current_api_key(request)
        if key is not None and key.rate_limit:
            return key.rate_limit
        return self.rate
