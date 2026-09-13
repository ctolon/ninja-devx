"""Type aliases describing what Django Ninja accepts, without ``typing.Any``."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from types import GenericAlias, MappingProxyType, UnionType
from typing import Final, TypeAlias

from django.http import HttpRequest
from ninja.constants import NOT_SET_TYPE
from ninja.security.base import AuthBase
from ninja.throttling import BaseThrottle

__all__ = [
    "AuthCallable",
    "AuthSpec",
    "JSONValue",
    "MethodFunction",
    "ResponseSchema",
    "ResponseSpec",
    "ThrottleSpec",
    "ViewDecorator",
    "ViewFunction",
    "did_you_mean",
    "status_phrase",
]

JSONValue: TypeAlias = (
    str | int | float | bool | Sequence["JSONValue"] | Mapping[str, "JSONValue"] | None
)

AuthCallable: TypeAlias = Callable[[HttpRequest], object]
"""A callable returning a truthy value (stored as ``request.auth``) when authenticated."""

AuthSpec: TypeAlias = (
    AuthBase | AuthCallable | Sequence[AuthBase | AuthCallable] | NOT_SET_TYPE | None
)
"""``None`` disables authentication, ``NOT_SET`` inherits it."""

ThrottleSpec: TypeAlias = BaseThrottle | Sequence[BaseThrottle] | NOT_SET_TYPE

ResponseSchema: TypeAlias = type[object] | UnionType | GenericAlias | None
"""A schema class, ``list[Schema]``, ``A | B`` or ``None`` (empty body)."""

ResponseSpec: TypeAlias = (
    ResponseSchema | Mapping[int | frozenset[int], ResponseSchema] | NOT_SET_TYPE
)
"""A single schema or a mapping of status codes (``ninja.responses.codes_4xx``...) to schemas."""

MethodFunction: TypeAlias = Callable[..., object]
"""An unbound controller method."""

ViewFunction: TypeAlias = Callable[..., object]
ViewDecorator: TypeAlias = Callable[[ViewFunction], ViewFunction]
"""A Django Ninja view decorator such as ``ninja.pagination.paginate(...)``."""


_PHRASES: Final[Mapping[int, str]] = MappingProxyType(
    {
        413: "Content Too Large",
        422: "Unprocessable Content",
    }
)


def did_you_mean(name: str, candidates: Iterable[str]) -> str:
    """`` Did you mean 'title'?`` when a close candidate exists, else an empty string."""
    import difflib

    matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
    return f" Did you mean {matches[0]!r}?" if matches else ""


def status_phrase(status: int) -> str:
    """The RFC 9110 reason phrase, the same on every Python version (3.13 renamed some)."""
    from http import HTTPStatus

    try:
        return _PHRASES.get(status) or HTTPStatus(status).phrase
    except ValueError:
        return "Error"
