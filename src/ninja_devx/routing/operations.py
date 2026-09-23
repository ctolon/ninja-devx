"""Operation decorators for controller methods.

The decorators only attach metadata; nothing is registered until
``Controller.as_router()`` runs. Options mirror ``ninja.Router.api_operation``
one-to-one, plus ``permissions`` and ``decorators``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Concatenate, Literal, ParamSpec, TypedDict, TypeVar, Unpack

from django.http import HttpRequest

from .._internal.types import AuthSpec, JSONValue, ResponseSpec, ThrottleSpec, ViewDecorator

if TYPE_CHECKING:
    from ..http.errors import ErrorMap
    from ..security.permissions import AnyPermission
    from .controller import Controller
    from .hooks import AsyncOperationHook, OperationHook

__all__ = [
    "OperationOptions",
    "OperationSpec",
    "api_operation",
    "async_variant",
    "delete",
    "get",
    "get_operation_specs",
    "patch",
    "post",
    "put",
    "query",
]

C = TypeVar("C", bound="Controller")
RequestT = TypeVar("RequestT", bound=HttpRequest)
P = ParamSpec("P")
R = TypeVar("R")

OperationMethod = Callable[Concatenate[C, RequestT, P], R]
"""A controller method: ``(self, request, *params) -> R``."""

OPERATIONS_ATTR = "__ninja_devx_operations__"
ASYNC_VARIANT_ATTR = "__ninja_devx_async_variant__"
AsyncF = TypeVar("AsyncF", bound=Callable[..., object])


def async_variant(sync_method: Callable[..., object]) -> Callable[[AsyncF], AsyncF]:
    """Declare the async implementation of an operation, used when the controller runs async.

    Reusable bases (like the CRUD mixins) define both and let ``mode`` pick::

        @get("/")
        def list(self, request): ...

        @async_variant(list)
        async def alist(self, request): ...

    :param sync_method: The sync operation this async method implements.
    """

    def decorator(async_method: AsyncF) -> AsyncF:
        setattr(sync_method, ASYNC_VARIANT_ATTR, async_method.__name__)
        return async_method

    return decorator


class OperationOptions(TypedDict, total=False):
    """Keyword arguments accepted by every operation decorator."""

    auth: AuthSpec
    """Ninja authentication for this operation; overrides the controller's."""
    throttle: ThrottleSpec
    """Ninja throttles for this operation."""
    response: ResponseSpec
    """Response schema, or ``{status: schema}`` (Ninja)."""
    operation_id: str | None
    """OpenAPI operation id (default ``<controller>_<method>``)."""
    summary: str | None
    """OpenAPI summary."""
    description: str | None
    """OpenAPI description (default: the method docstring)."""
    tags: list[str] | None
    """OpenAPI tags."""
    deprecated: bool | None
    """Mark the operation deprecated in OpenAPI."""
    by_alias: bool | None
    """Serialize the response by field alias (Ninja)."""
    exclude_unset: bool | None
    """Leave unset fields out of the response (Ninja)."""
    exclude_defaults: bool | None
    """Leave fields equal to their default out of the response (Ninja)."""
    exclude_none: bool | None
    """Leave ``None`` fields out of the response (Ninja)."""
    url_name: str | None
    """Django URL name for ``reverse()``."""
    include_in_schema: bool
    """Show the operation in OpenAPI."""
    openapi_extra: dict[str, JSONValue] | None
    """Merged into the operation's OpenAPI object."""
    permissions: Sequence[AnyPermission]
    """Replaces the controller's permissions; ``Also(...)`` adds to them instead."""
    decorators: Sequence[ViewDecorator]
    """View decorators applied inside the controller's ones; the first item is outermost."""
    hooks: Sequence[OperationHook | AsyncOperationHook]
    """Operation hooks run inside the controller's hooks."""
    atomic: bool | Literal["durable"]
    """Run the operation in ``transaction.atomic()`` (``"durable"``: must be the outermost)."""
    database: str
    """Database alias for ``atomic``."""
    errors: ErrorMap
    """Exception rules for this operation, over the controller's."""
    raises: Sequence[type[BaseException]]
    """Exceptions this operation may raise: documented in OpenAPI, checked against the rules."""
    meta: Sequence[object]
    """Typed metadata read by permissions and hooks via ``get_operation(request).meta(Kind)``."""
    document_errors: bool
    """Document 401/403/404/422 responses in OpenAPI (default from settings)."""


class RouteOptions(OperationOptions, total=False):
    """Per-operation overrides in ``Controller.routes`` (paths and options are not hard-coded)."""

    path: str
    """Replaces the declared path, e.g. ``{"restore": {"path": "/{pk}/undelete"}}``."""
    enabled: bool
    """``False`` removes the operation from the router."""


@dataclass(frozen=True, slots=True)
class OperationSpec:
    methods: tuple[str, ...]
    path: str
    options: OperationOptions

    def with_options(self, **options: Unpack[OperationOptions]) -> OperationSpec:
        return replace(self, options={**self.options, **options})


def get_operation_specs(func: object) -> tuple[OperationSpec, ...]:
    specs: tuple[OperationSpec, ...] = getattr(func, OPERATIONS_ATTR, ())
    return specs


def api_operation(
    methods: str | Sequence[str],
    path: str = "/",
    **options: Unpack[OperationOptions],
) -> Callable[[OperationMethod[C, RequestT, P, R]], OperationMethod[C, RequestT, P, R]]:
    """Mark a controller method as an operation for the given HTTP methods.

    Can be stacked to expose the same method under several paths.

    :param methods: HTTP methods, e.g. ``["GET", "HEAD"]``.
    :param path: Path relative to the router; ``{name}`` segments become parameters.
    :param options: ``OperationOptions``: Ninja's operation keywords plus ninja-devx's.
    """
    normalized = (methods,) if isinstance(methods, str) else tuple(methods)
    spec = OperationSpec(
        methods=tuple(method.upper() for method in normalized),
        path=path,
        options=options,
    )

    def decorator(func: OperationMethod[C, RequestT, P, R]) -> OperationMethod[C, RequestT, P, R]:
        # Decorators run bottom-up; prepending keeps specs in source order.
        setattr(func, OPERATIONS_ATTR, (spec, *get_operation_specs(func)))
        return func

    return decorator


def get(
    path: str = "/", **options: Unpack[OperationOptions]
) -> Callable[[OperationMethod[C, RequestT, P, R]], OperationMethod[C, RequestT, P, R]]:
    return api_operation("GET", path, **options)


def post(
    path: str = "/", **options: Unpack[OperationOptions]
) -> Callable[[OperationMethod[C, RequestT, P, R]], OperationMethod[C, RequestT, P, R]]:
    return api_operation("POST", path, **options)


def put(
    path: str = "/", **options: Unpack[OperationOptions]
) -> Callable[[OperationMethod[C, RequestT, P, R]], OperationMethod[C, RequestT, P, R]]:
    return api_operation("PUT", path, **options)


def patch(
    path: str = "/", **options: Unpack[OperationOptions]
) -> Callable[[OperationMethod[C, RequestT, P, R]], OperationMethod[C, RequestT, P, R]]:
    return api_operation("PATCH", path, **options)


def delete(
    path: str = "/", **options: Unpack[OperationOptions]
) -> Callable[[OperationMethod[C, RequestT, P, R]], OperationMethod[C, RequestT, P, R]]:
    return api_operation("DELETE", path, **options)


def query(
    path: str = "/", **options: Unpack[OperationOptions]
) -> Callable[[OperationMethod[C, RequestT, P, R]], OperationMethod[C, RequestT, P, R]]:
    """An HTTP ``QUERY`` operation: safe and idempotent like ``GET``, with a request body.

    Permissions treat it as a read (``view``). The OpenAPI document lists it under the
    ``query`` key that OpenAPI 3.2 defines; tools that only know 3.1 skip it.

    TODO: Django has no QUERY support of its own yet. ``CsrfViewMiddleware`` treats it as
    unsafe (session-authenticated calls need the CSRF token) and ``django.test.Client`` has
    no ``query()`` (use ``client.request("QUERY", ...)``). Revisit when Django adds it.
    """
    return api_operation("QUERY", path, **options)
