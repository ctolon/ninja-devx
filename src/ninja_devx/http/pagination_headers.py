"""Expose pagination metadata as response headers (RFC 8288 ``Link``, ``X-Total-Count``).

Add it where the paginated controller is mounted::

    use_middleware(api, PaginationHeadersMiddleware())

``Link`` carries ``rel="next"``/``rel="prev"`` for both paginators; ``X-Total-Count`` is set
when limit/offset pagination computed a count. A ``count`` option that only counts up to a
threshold sends ``X-Total-Count: N+`` past it, instead of ``N``.
"""

from __future__ import annotations

from typing import Final, cast

from django.http import HttpRequest, HttpResponseBase

from .middleware import Middleware

__all__ = ["PAGINATION_ATTR", "PaginationHeadersMiddleware"]

PAGINATION_ATTR: Final = "_ninja_devx_pagination"
"""Request attribute a paginator fills with ``count``/``next``/``previous``."""


class PaginationHeadersMiddleware(Middleware):
    """Copy pagination metadata recorded by the paginator onto response headers."""

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        meta: object = request.__dict__.get(PAGINATION_ATTR)
        if not isinstance(meta, dict):
            return response
        data = cast("dict[str, object]", meta)
        count = data.get("count")
        if isinstance(count, int) and "X-Total-Count" not in response:
            suffix = "+" if data.get("count_lower_bound") else ""
            response["X-Total-Count"] = f"{count}{suffix}"
        links: list[str] = []
        for rel, key in (("next", "next"), ("prev", "previous")):
            url = data.get(key)
            if isinstance(url, str) and url:
                links.append(f'<{url}>; rel="{rel}"')
        if links and "Link" not in response:
            response["Link"] = ", ".join(links)
        return response
