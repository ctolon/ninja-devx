"""Request hardening: body size, content type and JSON nesting limits.

Each is a :class:`~ninja_devx.http.middleware.Middleware` returning a short-circuiting
response in the package's error format::

    use_middleware(
        api,
        MaxBodySizeMiddleware(5_000_000),
        EnforceContentTypeMiddleware({"application/json"}),
        JsonDepthMiddleware(max_depth=32),
    )
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Final, cast

from django.http import HttpRequest, HttpResponse

from .errors import render
from .middleware import Middleware

__all__ = ["EnforceContentTypeMiddleware", "JsonDepthMiddleware", "MaxBodySizeMiddleware"]

_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH"})
_CONTENT_TYPE_HEADER: Final = "CONTENT_TYPE"


def _reject(status: int, code: str, detail: str) -> HttpResponse:
    return render(status, code, {"detail": detail, "code": code})


class MaxBodySizeMiddleware(Middleware):
    """Reject requests whose ``Content-Length`` exceeds ``max_bytes`` with 413.

    Only the declared length is checked; chunked bodies without one are bounded by
    Django's ``DATA_UPLOAD_MAX_MEMORY_SIZE``.

    :param max_bytes: Maximum accepted body size in bytes.
    """

    def __init__(self, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes

    def process_request(self, request: HttpRequest) -> HttpResponse | None:
        length = request.headers.get("Content-Length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            return _reject(413, "request_too_large", "Request body is too large.")
        return None


class EnforceContentTypeMiddleware(Middleware):
    """Reject write requests whose media type is not allowed, with 415.

    A request without a ``Content-Type`` header passes.

    :param media_types: Accepted media types (parameters such as ``; charset=`` ignored).
    :param methods: Methods to check (default: POST/PUT/PATCH).
    """

    def __init__(
        self, media_types: Collection[str], *, methods: Collection[str] = _BODY_METHODS
    ) -> None:
        self.media_types = {value.lower() for value in media_types}
        self.methods = {value.upper() for value in methods}

    def process_request(self, request: HttpRequest) -> HttpResponse | None:
        if request.method not in self.methods:
            return None
        content_type = request.META.get(_CONTENT_TYPE_HEADER, "").split(";", 1)[0].strip().lower()
        if content_type and content_type not in self.media_types:
            return _reject(415, "unsupported_media_type", "Unsupported request media type.")
        return None


def _exceeds(value: object, max_depth: int) -> bool:
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        children: Collection[object]
        if isinstance(item, dict):
            children = cast("dict[object, object]", item).values()
        elif isinstance(item, list):
            children = cast("list[object]", item)
        else:
            continue
        if children and depth + 1 > max_depth:
            return True
        pending.extend((child, depth + 1) for child in children)
    return False


class JsonDepthMiddleware(Middleware):
    """Reject JSON bodies nested deeper than ``max_depth`` with 400.

    :param max_depth: Maximum nesting depth of objects and arrays.
    """

    def __init__(self, max_depth: int = 32) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be positive")
        self.max_depth = max_depth

    def process_request(self, request: HttpRequest) -> HttpResponse | None:
        if request.method not in _BODY_METHODS:
            return None
        content_type = request.META.get(_CONTENT_TYPE_HEADER, "")
        if "json" not in content_type.split(";", 1)[0].lower():
            return None
        if not request.body:
            return None
        try:
            payload: object = json.loads(request.body)
        except RecursionError:
            return self._too_deep()
        except ValueError:
            return None
        if _exceeds(payload, self.max_depth):
            return self._too_deep()
        return None

    @staticmethod
    def _too_deep() -> HttpResponse:
        return _reject(400, "json_too_deep", "JSON body is nested too deeply.")
