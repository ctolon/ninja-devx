"""Domain errors that carry their HTTP meaning without importing any web code.

Raise them from services, repositories and policies; ``ninja_devx.http.errors.ErrorMap``
turns anything with ``http_status`` and ``code`` into a response. Your own exceptions
can do the same by defining those two class attributes (see ``HttpMappable``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar, Protocol, TypeAlias, runtime_checkable

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

__all__ = [
    "Conflict",
    "DomainError",
    "HttpMappable",
    "NotFound",
    "PermissionDenied",
    "ValidationFailed",
]

JSON: TypeAlias = str | int | float | bool | Sequence["JSON"] | Mapping[str, "JSON"] | None


@runtime_checkable
class HttpMappable(Protocol):
    """An exception that knows its HTTP status and machine-readable code."""

    http_status: ClassVar[int]
    """HTTP status of the response."""
    code: ClassVar[str]
    """Machine-readable error code."""

    def error_body(self) -> Mapping[str, JSON]: ...


class DomainError(Exception):
    """Base class for business errors: 400 unless a subclass says otherwise."""

    http_status: ClassVar[int] = 400
    """HTTP status when mapped (400 unless overridden)."""
    code: ClassVar[str] = "domain_error"
    """Machine-readable error code."""
    default_message: ClassVar[str | None] = None
    """``detail`` when none is given, translated with ``gettext`` (default: the docstring)."""

    def __init__(self, message: str = "", **details: JSON) -> None:
        """
        :param message: The ``detail`` (default: ``default_message``, else the docstring).
        :param details: Extra JSON fields merged into the body.
        """
        default = self.default_message or self.__class__.__doc__
        super().__init__(message or (_(default) if default else self.code))
        self.message = str(self.args[0])
        self.details: Mapping[str, JSON] = details

    def error_body(self) -> Mapping[str, JSON]:
        return {"detail": self.message, "code": self.code, **self.details}


class NotFound(DomainError):
    """Not found."""

    default_message = gettext_noop("Not found.")

    http_status = 404
    code = "not_found"


class Conflict(DomainError):
    """The request conflicts with the current state."""

    default_message = gettext_noop("The request conflicts with the current state.")

    http_status = 409
    code = "conflict"


class PermissionDenied(DomainError):
    """You do not have permission to perform this action."""

    default_message = gettext_noop("You do not have permission to perform this action.")

    http_status = 403
    code = "permission_denied"


class ValidationFailed(DomainError):
    """Validation failed."""

    default_message = gettext_noop("Validation failed.")

    http_status = 422
    code = "validation_failed"

    def __init__(
        self, message: str = "", *, errors: Mapping[str, Sequence[str]] | None = None
    ) -> None:
        super().__init__(message)
        self.errors: Mapping[str, Sequence[str]] = errors or {}

    def error_body(self) -> Mapping[str, JSON]:
        return {
            "detail": [
                {"type": self.code, "loc": ["body", field], "msg": message}
                for field, messages in self.errors.items()
                for message in messages
            ]
            or self.message
        }
