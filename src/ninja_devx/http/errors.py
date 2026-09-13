"""Map exceptions (domain errors, Django errors, your own) to HTTP responses.

Rules layer like options: operation ``errors`` → controller ``errors`` →
``NINJA_DEVX["ERRORS"]`` → the defaults. Exceptions implementing
``ninja_devx.layers.HttpMappable`` (every ``DomainError``) map themselves.

At the API level ``ErrorMap.install(api)`` registers the same rules with Ninja's own
``api.add_exception_handler``; ``mount(api, ..., errors=...)`` does it for you.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import NinjaAPI
from ninja.responses import NinjaJSONEncoder

from .._internal.types import JSONValue, status_phrase
from ..configuration.settings import get_settings
from ..layers.persistence import validation_failed

__all__ = ["ErrorFormat", "ErrorMap", "ErrorRule"]

E = TypeVar("E", bound=BaseException)
ErrorFormat = Literal["ninja", "problem+json"]


@dataclass(frozen=True, slots=True)
class ErrorRule(Generic[E]):
    exception: type[E]
    """Exception class (subclasses match too)."""
    status: int
    """HTTP status."""
    code: str
    """Machine-readable code."""
    body: Callable[[E], Mapping[str, JSONValue]] | None = None
    """Builds the body from the exception (default ``{detail, code}``)."""

    def render_body(self, exc: E) -> dict[str, JSONValue]:
        if self.body is not None:
            return dict(self.body(exc))
        error_body: Callable[[], Mapping[str, JSONValue]] | None = getattr(exc, "error_body", None)
        if error_body is not None and _is_mappable_class(type(exc)):
            body = dict(error_body())
            body.setdefault("code", self.code)
            return body
        message = str(exc) or status_phrase(self.status)
        return {"detail": message, "code": self.code}


AnyRule = ErrorRule[BaseException]


class ErrorMap:
    """An immutable set of exception → response rules. Later rules win."""

    __slots__ = ("_rules",)

    def __init__(self, rules: Iterable[AnyRule] = ()) -> None:
        self._rules: tuple[AnyRule, ...] = tuple(rules)

    @classmethod
    def django_defaults(cls) -> ErrorMap:
        """Django's ``ValidationError`` → 422, ``ObjectDoesNotExist`` → 404,
        ``PermissionDenied`` → 403."""
        return (
            cls()
            .map(
                DjangoValidationError,
                422,
                code="validation_failed",
                body=lambda exc: validation_failed(exc).error_body(),
            )
            .map(ObjectDoesNotExist, 404, code="not_found")
            .map(DjangoPermissionDenied, 403, code="permission_denied")
        )

    def map(
        self,
        exception: type[E],
        status: int,
        *,
        code: str | None = None,
        body: Callable[[E], Mapping[str, JSONValue]] | None = None,
    ) -> ErrorMap:
        """A new map where ``exception`` (and subclasses) produce ``status``.

        :param exception: Exception class; subclasses match too.
        :param status: HTTP status of the response.
        :param code: Machine-readable code (default: the class name in snake_case).
        :param body: Builds the body from the exception (default ``{detail, code}`` or
            ``error_body()``).
        """
        rule = ErrorRule(exception, status, code or _snake(exception.__name__), body)
        return ErrorMap((*self._rules, cast(AnyRule, rule)))

    def __or__(self, other: ErrorMap) -> ErrorMap:
        return ErrorMap((*self._rules, *other.rules))

    @property
    def rules(self) -> tuple[AnyRule, ...]:
        return self._rules

    def __bool__(self) -> bool:
        return bool(self._rules)

    def rule_for(self, exception: type[BaseException]) -> AnyRule | None:
        """The rule for ``exception``: closest class in its MRO, later rules first."""
        for klass in exception.__mro__:
            for rule in reversed(self._rules):
                if rule.exception is klass:
                    return rule
        if _is_mappable_class(exception):
            status: int = getattr(exception, "http_status")  # noqa: B009
            code: str = getattr(exception, "code")  # noqa: B009
            return ErrorRule(exception, status, code)
        return None

    def response(self, request: HttpRequest, exc: BaseException) -> HttpResponse | None:
        rule = self.rule_for(type(exc))
        if rule is None:
            return None
        return render(rule.status, rule.code, rule.render_body(exc))

    def install(self, api: NinjaAPI) -> None:
        """Register the rules with Ninja's ``api.add_exception_handler``."""
        for rule in self._rules:
            exception = rule.exception
            if issubclass(exception, Exception):
                api.add_exception_handler(exception, _handler(rule))


def render(status: int, code: str, body: Mapping[str, JSONValue]) -> HttpResponse:
    error_format: ErrorFormat = get_settings().error_format
    if error_format == "problem+json":
        detail = body.get("detail")
        problem: dict[str, JSONValue] = {
            "type": "about:blank",
            "title": status_phrase(status),
            "status": status,
            "code": code,
            **{key: value for key, value in body.items() if key != "detail"},
        }
        if detail is not None:
            problem["detail"] = detail
        return JsonResponse(
            problem,
            status=status,
            encoder=NinjaJSONEncoder,
            content_type="application/problem+json",
        )
    return JsonResponse(dict(body), status=status, encoder=NinjaJSONEncoder)


def _handler(
    rule: AnyRule,
) -> Callable[[HttpRequest, Exception | type[Exception]], HttpResponse]:
    def handle(request: HttpRequest, exc: Exception | type[Exception]) -> HttpResponse:
        instance = exc if isinstance(exc, Exception) else exc()
        return render(rule.status, rule.code, rule.render_body(instance))

    return handle


def _is_mappable_class(exception: type[BaseException]) -> bool:
    return isinstance(getattr(exception, "http_status", None), int) and isinstance(
        getattr(exception, "code", None), str
    )


def _snake(name: str) -> str:
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
