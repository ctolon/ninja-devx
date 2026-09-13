"""Conditional requests (RFC 9110): ``ETag``, ``If-None-Match`` → 304, ``If-Match`` → 412.

Model controllers enable them with ``etag``::

    class PostController(CRUDController[Post, PostOut, PostIn]):
        etag = ETag()                        # tag = hash of the rendered representation
        etag = ETag(field="updated_at")      # tag = the version field: no extra serialization
        etag = ETag(require_if_match=True)   # writes without If-Match get 428

- ``GET /{pk}`` sends ``ETag`` and answers ``If-None-Match`` with 304 before serializing.
- ``GET /`` sends an ``ETag`` of the rendered page and answers ``If-None-Match`` with 304.
- ``PUT``/``PATCH``/``DELETE`` compare ``If-Match`` with the current object and fail with
  412 when it changed in between (optimistic locking); successful writes send the new tag.

Any other operation can use the response-level version: ``decorators=[conditional()]``.
Weak comparison is used for ``If-None-Match`` (RFC 9110 §13.1.2); ``If-Match: *`` always passes.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, TypeVar, cast

from django.http import HttpRequest, HttpResponseBase, HttpResponseNotModified
from django.utils.http import parse_etags, quote_etag
from django.utils.translation import gettext_noop
from ninja.decorators import decorate_view
from pydantic import BaseModel

from ..layers.errors import DomainError

__all__ = [
    "ETag",
    "PreconditionFailed",
    "PreconditionRequired",
    "conditional",
    "entity_tag",
    "remember_etag",
]

F = TypeVar("F", bound=Callable[..., object])
_ETAG_ATTR: Final = "_ninja_devx_etag"
_SAFE: Final = frozenset({"GET", "HEAD"})


class PreconditionFailed(DomainError):
    """The resource changed since you last fetched it."""

    default_message = gettext_noop("The resource changed since you last fetched it.")

    http_status = 412
    code = "precondition_failed"


class PreconditionRequired(DomainError):
    """Send If-Match with the ETag you last received."""

    default_message = gettext_noop("Send If-Match with the ETag you last received.")

    http_status = 428
    code = "precondition_required"


@dataclass(frozen=True, slots=True)
class ETag:
    field: str | None = None
    """A field that changes on every write (``updated_at``, ``version``). ``None`` hashes the
    output schema's representation instead, which costs one extra serialization per check."""
    weak: bool = False
    """Weak tags (``W/"..."``) survive compression and JSON formatting differences."""
    require_if_match: bool = False
    """Writes without ``If-Match`` fail with 428 Precondition Required."""
    lists: bool = True
    """Also tag list responses (from their rendered body)."""


def entity_tag(value: object, *, weak: bool = True) -> str:
    """A quoted entity tag for ``value`` (strings, numbers, dates, JSON-like data)."""
    if isinstance(value, bytes):
        payload = value
    else:
        payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    digest = hashlib.blake2b(payload, digest_size=16).hexdigest()
    tag = quote_etag(digest)
    return f"W/{tag}" if weak else tag


def representation_tag(
    instance: object,
    schema: type[BaseModel] | None,
    config: ETag,
    pk: object,
    *,
    request: HttpRequest | None = None,
) -> str:
    if config.field is not None:
        return entity_tag([str(pk), getattr(instance, config.field)], weak=config.weak)
    if schema is None:
        return entity_tag([str(pk)], weak=config.weak)
    context = {"request": request}
    data = schema.model_validate(instance, from_attributes=True, context=context).model_dump(
        mode="json", context=context
    )
    return entity_tag(data, weak=config.weak)


def remember_etag(request: HttpRequest, tag: str) -> None:
    """Send ``tag`` as the response's ``ETag`` (used by ``conditional()``-wrapped views)."""
    request.__dict__[_ETAG_ATTR] = tag


def not_modified(request: HttpRequest, tag: str) -> HttpResponseNotModified | None:
    """A 304 when ``If-None-Match`` matches ``tag`` on a safe request."""
    if request.method not in _SAFE:
        return None
    header = request.headers.get("If-None-Match")
    if header and _matches(header, tag, weak=True):
        response = HttpResponseNotModified()
        response["ETag"] = tag
        return response
    return None


def check_if_match(request: HttpRequest, tag: str, *, required: bool) -> None:
    """412 when ``If-Match`` does not match ``tag``; 428 when required and missing."""
    header = request.headers.get("If-Match")
    if header is None:
        if required:
            raise PreconditionRequired("Send If-Match with the ETag you last received")
        return
    if not _matches(header, tag, weak=False):
        raise PreconditionFailed("The resource changed since you last fetched it", etag=tag)


def _matches(header: str, tag: str, *, weak: bool) -> bool:
    candidates: list[str] = parse_etags(header)
    if "*" in candidates:
        return True

    if not weak:
        return not tag.startswith("W/") and tag in candidates

    def strip(value: str) -> str:
        return value.removeprefix("W/") if weak else value

    return strip(tag) in {strip(candidate) for candidate in candidates}


def _finalize(
    request: HttpRequest, response: HttpResponseBase, *, from_body: bool
) -> HttpResponseBase:
    if not 200 <= response.status_code < 300 or response.streaming:
        return response
    tag: str | None = request.__dict__.get(_ETAG_ATTR)
    if tag is None and from_body and request.method in _SAFE and response.status_code == 200:
        tag = entity_tag(cast("bytes", getattr(response, "content", b"")))
    if tag is None:
        return response
    if not response.has_header("ETag"):
        response["ETag"] = tag
    if response.status_code == 200 and (unchanged := not_modified(request, tag)) is not None:
        for header in ("Cache-Control", "Vary", "Expires"):
            if response.has_header(header):
                unchanged[header] = response[header]
        return unchanged
    return response


def _response_decorator(
    *, from_body: bool
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    def wrap_run(run: Callable[..., object]) -> Callable[..., object]:
        if inspect.iscoroutinefunction(run):

            async def arun(request: HttpRequest, *args: object, **kwargs: object) -> object:
                awaitable = cast("Callable[..., Awaitable[HttpResponseBase]]", run)
                response = await awaitable(request, *args, **kwargs)
                return _finalize(request, response, from_body=from_body)

            return arun

        def srun(request: HttpRequest, *args: object, **kwargs: object) -> object:
            response = cast("Callable[..., HttpResponseBase]", run)(request, *args, **kwargs)
            return _finalize(request, response, from_body=from_body)

        return srun

    return wrap_run


def conditional(*, from_body: bool = True) -> Callable[[F], F]:
    """An operation decorator adding ``ETag`` and 304 handling to successful GET responses.

    With ``from_body`` the tag hashes the rendered response; otherwise only tags set with
    ``remember_etag`` are used.

    :param from_body: Hash the rendered body when no tag was set with ``remember_etag``.
    """

    def decorator(handler: F) -> F:
        return decorate_view(_response_decorator(from_body=from_body))(handler)

    return decorator
