"""Rate limits for Ninja's ``throttle=`` option: per user, per client, per tenant, per scope.

::

    NINJA_DEVX = {"THROTTLE_RATES": {"uploads": "10/min", "search": "120/min"}}

    class UploadController(Controller):
        options = ControllerOptions(throttle=[UserRateThrottle("1000/day")])

        @post("/", throttle=[ScopedRateThrottle("uploads"), ClientRateThrottle(anon="5/min")])
        def upload(self, request: HttpRequest, file: UploadedFile) -> Upload: ...

They plug into Ninja's own throttling, so rejected requests get Ninja's 429 with a
``Retry-After`` header. Unlike Ninja's built-ins they:

- keep no per-request state on the (shared) throttle object, so they are thread-safe;
- count in fixed windows through a ``ThrottleStorage``, so limits hold across processes;
- read ``NINJA_DEVX["THROTTLE_RATES"]`` at request time, so ``override_settings`` works;
- accept rates like ``"100/min"``, ``"1000/day"`` or ``"20/5min"``.

The default storage counts with ``cache.add`` + ``cache.incr``, which is atomic on Redis
and Memcached but not elsewhere (a crash between the two calls loses one hit). Pass
``storage=`` (or set ``NINJA_DEVX["THROTTLE_STORAGE"]``) to plug in something stricter,
such as ``ninja_devx.contrib.redis_throttle.RedisThrottleStorage``.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.utils.module_loading import import_string
from ninja.throttling import BaseThrottle

from ..configuration.settings import SETTINGS_NAME, get_settings
from ..security.auth import request_user
from ..security.tenancy import current_tenant
from .middleware import record_rate_limit

__all__ = [
    "AnonRateThrottle",
    "CacheThrottleStorage",
    "ClientRateThrottle",
    "RateThrottle",
    "ScopedRateThrottle",
    "TenantRateThrottle",
    "ThrottleStorage",
    "UserRateThrottle",
    "parse_rate",
]

_PERIODS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "s": 1,
        "sec": 1,
        "second": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "h": 3600,
        "hour": 3600,
        "d": 86400,
        "day": 86400,
    }
)


def parse_rate(rate: str) -> tuple[int, int]:
    """``"100/min"`` → ``(100, 60)``; ``"20/5min"`` → ``(20, 300)``.

    :param rate: Requests per period, e.g. ``"100/min"`` or ``"20/5min"``.
    """
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)?([a-z]+)", rate)
    if match is None or match[3] not in _PERIODS:
        raise ImproperlyConfigured(f"Invalid throttle rate {rate!r}; use e.g. '100/min'")
    return int(match[1]), int(match[2] or 1) * _PERIODS[match[3]]


@runtime_checkable
class ThrottleStorage(Protocol):
    """Where throttles count hits. ``key`` already identifies the current fixed window."""

    def hit(self, key: str, window_seconds: int) -> tuple[int, float]:
        """Count one hit against ``key``, creating its window (``window_seconds`` TTL) on
        the first hit, and return ``(count, retry_after)``: the count including this hit,
        and the seconds remaining until the window resets.

        :param key: Unique to the throttle, identity and current window.
        :param window_seconds: The window's length, used as the new key's TTL.
        """
        ...


@dataclass(frozen=True, slots=True)
class CacheThrottleStorage:
    """Default storage: a Django cache, via ``cache.add`` + ``cache.incr``.

    Atomic on Redis and Memcached, so limits hold across processes; on other backends a
    crash between the two calls can lose one hit.
    """

    cache_alias: str = "default"
    """Cache alias (``settings.CACHES``) counters are stored under."""

    def hit(self, key: str, window_seconds: int) -> tuple[int, float]:
        cache = caches[self.cache_alias]
        cache.add(key, 0, timeout=window_seconds + 1)
        try:
            count = cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.set(key, 1, timeout=window_seconds + 1)
            count = 1
        return count, window_seconds - (time.time() % window_seconds)


def _resolve_storage(explicit: ThrottleStorage | None, cache_alias: str) -> ThrottleStorage:
    if explicit is not None:
        return explicit
    configured = get_settings().throttle_storage
    if configured is None:
        return CacheThrottleStorage(cache_alias)
    resolved: object = import_string(configured) if isinstance(configured, str) else configured
    if not isinstance(resolved, ThrottleStorage):
        raise ImproperlyConfigured(f"{SETTINGS_NAME}['THROTTLE_STORAGE'] is not a ThrottleStorage")
    return resolved


class RateThrottle(BaseThrottle):
    """Base class: ``rate`` requests per window for each identity from ``identify()``."""

    scope: str = "default"

    def __init__(
        self,
        rate: str | None = None,
        *,
        cache: str = "default",
        storage: ThrottleStorage | None = None,
    ) -> None:
        self.rate = rate
        self.cache_alias = cache
        self.storage = storage
        self._local = threading.local()
        if rate is not None:
            parse_rate(rate)  # fail at startup on typos

    def identify(self, request: HttpRequest) -> str | None:
        """The identity to count for, or ``None`` to not throttle this request."""
        raise NotImplementedError

    def get_rate(self, request: HttpRequest) -> str | None:
        return self.rate

    def allow_request(self, request: HttpRequest) -> bool:
        self._local.wait = None
        rate = self.get_rate(request)
        identity = self.identify(request)
        if rate is None or identity is None:
            return True
        limit, duration = parse_rate(rate)
        window = int(time.time() // duration)
        digest = hashlib.blake2b(identity.encode(), digest_size=12).hexdigest()
        key = f"ninja_devx:throttle:{self.scope}:{digest}:{duration}:{window}"
        storage = _resolve_storage(self.storage, self.cache_alias)
        count, retry_after = storage.hit(key, duration)
        record_rate_limit(
            request,
            policy=self.scope,
            limit=limit,
            remaining=limit - count,
            reset=retry_after,
            window=duration,
        )
        if count > limit:
            self._local.wait = retry_after
            return False
        return True

    def wait(self) -> float | None:
        wait: float | None = getattr(self._local, "wait", None)
        return wait

    def client_ip(self, request: HttpRequest) -> str:
        return self.get_ident(request) or "unknown"


def _user_pk(request: HttpRequest) -> object | None:
    auth: object = getattr(request, "auth", None)
    candidate = auth if getattr(auth, "pk", None) is not None else request_user(request)
    return getattr(candidate, "pk", None)


class UserRateThrottle(RateThrottle):
    """Per authenticated user (``request.auth`` or ``request.user``); anonymous requests pass."""

    scope = "user"

    def identify(self, request: HttpRequest) -> str | None:
        pk = _user_pk(request)
        return None if pk is None else f"user:{pk}"


class AnonRateThrottle(RateThrottle):
    """Per client IP for anonymous requests; authenticated requests pass."""

    scope = "anon"

    def identify(self, request: HttpRequest) -> str | None:
        return None if _user_pk(request) is not None else f"ip:{self.client_ip(request)}"


class ClientRateThrottle(RateThrottle):
    """One throttle, two rates: ``ClientRateThrottle(user="600/min", anon="30/min")``."""

    scope = "client"

    def __init__(
        self,
        *,
        user: str | None = None,
        anon: str | None = None,
        cache: str = "default",
        storage: ThrottleStorage | None = None,
    ) -> None:
        super().__init__(None, cache=cache, storage=storage)
        self.user_rate = user
        self.anon_rate = anon
        for rate in (user, anon):
            if rate is not None:
                parse_rate(rate)

    def get_rate(self, request: HttpRequest) -> str | None:
        return self.user_rate if _user_pk(request) is not None else self.anon_rate

    def identify(self, request: HttpRequest) -> str | None:
        pk = _user_pk(request)
        return f"user:{pk}" if pk is not None else f"ip:{self.client_ip(request)}"


class ScopedRateThrottle(ClientRateThrottle):
    """A named limit whose rate comes from ``NINJA_DEVX["THROTTLE_RATES"][scope]``.

    Each user (or anonymous client IP) has its own counter per scope, so
    ``ScopedRateThrottle("uploads")`` on several operations shares one budget.
    """

    def __init__(
        self, scope: str, *, cache: str = "default", storage: ThrottleStorage | None = None
    ) -> None:
        super().__init__(cache=cache, storage=storage)
        self.scope = scope

    def get_rate(self, request: HttpRequest) -> str | None:
        rates = get_settings().throttle_rates
        if self.scope not in rates:
            raise ImproperlyConfigured(
                f"No rate for throttle scope {self.scope!r} in {SETTINGS_NAME}['THROTTLE_RATES']"
            )
        return rates[self.scope]


class TenantRateThrottle(RateThrottle):
    """Per tenant: the tenant already resolved, ``request.tenant``, or ``tenant(request)``."""

    scope = "tenant"

    def __init__(
        self,
        rate: str,
        *,
        tenant: Callable[[HttpRequest], object] | None = None,
        cache: str = "default",
        storage: ThrottleStorage | None = None,
    ) -> None:
        super().__init__(rate, cache=cache, storage=storage)
        self.tenant = tenant

    def identify(self, request: HttpRequest) -> str | None:
        found = current_tenant(request) or getattr(request, "tenant", None)
        if found is None:
            resolver = self.tenant or get_settings().tenant_resolver
            found = resolver(request) if resolver is not None else None
        if found is None:
            return None
        return f"tenant:{getattr(found, 'pk', found)}"
