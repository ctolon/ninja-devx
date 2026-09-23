"""Cache whole HTTP responses for read operations.

``ResponseCacheMiddleware`` caches the body, status and content type of matching
responses and adds ``Cache-Control``/``Vary``. Install it on an API, router or controller::

    use_middleware(api, ResponseCacheMiddleware(ttl=60, vary_on=("Authorization",)))

Invalidate every cached response sharing a prefix with ``invalidate_cache`` after a write.
Cache calls are synchronous, also for async operations.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from typing import Final, cast

from django.core.cache import caches
from django.core.cache.backends.base import BaseCache
from django.http import HttpRequest, HttpResponse, HttpResponseBase, StreamingHttpResponse

from .middleware import Middleware

__all__ = ["ResponseCacheMiddleware", "invalidate_cache"]

_CACHEABLE_STATUSES: Final = frozenset({200, 203, 300, 301, 404, 410})
_CREDENTIAL_HEADERS: Final = frozenset({"authorization", "cookie"})
_KEY_ATTR: Final = "_ninja_devx_response_cache_key"
_VERSION_TEMPLATE: Final = "ninja_devx:cache-version:{prefix}"


def _version(backend: BaseCache, prefix: str) -> int:
    key = _VERSION_TEMPLATE.format(prefix=prefix)
    return cast("int", backend.get_or_set(key, 1, timeout=None))


def invalidate_cache(prefix: str = "", *, cache: str = "default") -> None:
    """Invalidate every cached response under ``prefix``.

    :param prefix: The same prefix passed to ``ResponseCacheMiddleware``.
    :param cache: Cache alias.
    """
    backend = caches[cache]
    key = _VERSION_TEMPLATE.format(prefix=prefix)
    try:
        backend.incr(key)
    except ValueError:
        # The version was evicted; entries under the old value must not be served again.
        backend.set(key, int(time.time()), timeout=None)


def _cacheable(response: HttpResponseBase) -> bool:
    return (
        not isinstance(response, StreamingHttpResponse)
        and response.status_code in _CACHEABLE_STATUSES
    )


class ResponseCacheMiddleware(Middleware):
    """Serve matching responses from the cache and store new ones.

    ``Cache-Control`` is ``private`` when ``vary_on`` names ``Authorization`` or ``Cookie``
    and ``public`` otherwise, unless the response already carries the header.

    :param ttl: Cache lifetime in seconds.
    :param vary_on: Header names that participate in the cache key (for example
        ``("Authorization", "Accept-Language")``).
    :param cache: Cache alias.
    :param key_prefix: Prefix used by ``invalidate_cache``.
    :param methods: HTTP methods to cache (others pass through).
    """

    def __init__(
        self,
        *,
        ttl: int = 60,
        vary_on: Sequence[str] = (),
        cache: str = "default",
        key_prefix: str = "",
        methods: Sequence[str] = ("GET", "HEAD"),
    ) -> None:
        self.ttl = ttl
        self.vary_on = tuple(vary_on)
        self.cache = cache
        self.key_prefix = key_prefix
        self.methods = {method.upper() for method in methods}
        scope = "private" if _CREDENTIAL_HEADERS & {name.lower() for name in vary_on} else "public"
        self.cache_control = f"{scope}, max-age={ttl}"

    def _key(self, request: HttpRequest) -> str:
        version = _version(caches[self.cache], self.key_prefix)
        parts = [request.method or "", request.get_full_path(), str(version)]
        parts += [f"{name}:{request.headers.get(name, '')}" for name in self.vary_on]
        digest = hashlib.blake2b("\n".join(parts).encode(), digest_size=16).hexdigest()
        return f"ninja_devx:response:{self.key_prefix}:{digest}"

    def process_request(self, request: HttpRequest) -> HttpResponseBase | None:
        if request.method not in self.methods:
            return None
        key = self._key(request)
        cached: object = caches[self.cache].get(key)
        if cached is None:
            request.__dict__[_KEY_ATTR] = key
            return None
        status, content, content_type = cast("tuple[int, bytes, str]", cached)
        response = HttpResponse(content, status=status, content_type=content_type or None)
        self._headers(response)
        response["X-Cache"] = "HIT"
        return response

    def _headers(self, response: HttpResponseBase) -> None:
        if self.vary_on and "Vary" not in response:
            response["Vary"] = ", ".join(self.vary_on)
        if "Cache-Control" not in response:
            response["Cache-Control"] = self.cache_control

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        if request.method not in self.methods or response.get("X-Cache") == "HIT":
            return response
        if not _cacheable(response):
            return response
        self._headers(response)
        key = request.__dict__.get(_KEY_ATTR) or self._key(request)
        caches[self.cache].set(
            key,
            (
                response.status_code,
                bytes(cast("HttpResponse", response).content),
                response.get("Content-Type", ""),
            ),
            timeout=self.ttl,
        )
        return response
