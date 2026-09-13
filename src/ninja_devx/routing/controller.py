"""Class-based controllers compiled into native Django Ninja routers."""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Literal,
    ParamSpec,
    TypedDict,
    TypeVar,
    Unpack,
    cast,
)

from asgiref.sync import sync_to_async
from django.db import transaction
from django.http import HttpRequest
from ninja import Router
from ninja.constants import NOT_SET

from .._internal.generics import type_arguments
from .._internal.types import AuthSpec, MethodFunction, ThrottleSpec, ViewDecorator, did_you_mean
from ..configuration.runtime import runtime_state
from ..configuration.settings import get_settings
from ..dependencies.container import ContainerLike
from ..dependencies.injection import resolve
from ..dependencies.instances import Scope, instance_provider
from ..exceptions import ControllerConfigError, MixedPathWarning
from ..http.errors import ErrorMap
from ..security.permissions import (
    AnyPermission,
    acheck_object_permissions,
    check_object_permissions,
)
from .bindings import ParameterBinding
from .compiler import Registration, path_specificity, throttle_spec
from .hooks import AsyncOperationHook, OperationHook, OperationInfo
from .operations import (
    ASYNC_VARIANT_ATTR,
    OperationOptions,
    OperationSpec,
    RouteOptions,
    get_operation_specs,
)

if TYPE_CHECKING:
    from django.core.checks import CheckMessage

    from ..http.middleware import Middleware
    from .plugins import ControllerPlugin

__all__ = ["BuiltRouter", "Controller", "ControllerOptions", "Scope", "built_router"]

T = TypeVar("T")
P = ParamSpec("P")
R = TypeVar("R")


class ControllerOptions(TypedDict, total=False):
    """Defaults for every operation of a controller.

    Operation-level ``auth``, ``throttle``, ``tags``, ``permissions``, ``atomic`` and
    ``deprecated`` replace these; ``decorators`` and ``hooks`` are combined (the
    controller's wrap the operation's).
    """

    auth: AuthSpec
    """Ninja authentication for every operation (``django_auth``, ``JWTAuth()``, a list, or
    ``None``)."""
    throttle: ThrottleSpec
    """Ninja throttles for every operation (``UserRateThrottle("100/min")`` or a list)."""
    tags: Sequence[str]
    """OpenAPI tags."""
    permissions: Sequence[AnyPermission]
    """Permissions checked before each operation (see ``Also`` for adding at operation level)."""
    decorators: Sequence[ViewDecorator]
    """View decorators (``paginate(...)``, ``conditional()``); the first item is outermost."""
    hooks: Sequence[OperationHook | AsyncOperationHook]
    """Operation hooks wrapping each call (sync ``around`` or async ``around_async``)."""
    atomic: bool | Literal["durable"]
    """Run sync operations in ``transaction.atomic()``; ``"durable"`` must be the outermost."""
    database: str
    """Database alias for ``atomic``."""
    errors: ErrorMap
    """Exception rules for every operation (see ``ninja_devx.http.errors``)."""
    meta: Sequence[object]
    """Typed metadata read with ``get_operation(request).meta(Kind)``."""
    plugins: Sequence[ControllerPlugin]
    """``ControllerPlugin`` objects applied to every operation."""
    middleware: Sequence[Middleware]
    """Router middleware around every operation, including auth and throttling
    (``RequestIDMiddleware()``, ``DeprecationMiddleware(sunset=...)``)."""
    allow_mixed_path: bool
    """Silence the warning for a path served by both sync and async operations."""
    deprecated: bool
    """Mark every operation deprecated in OpenAPI."""
    document_errors: bool
    """Document 401/403/404/422 responses in OpenAPI (default from settings)."""
    by_alias: bool
    """Serialize responses by field alias (Ninja)."""
    exclude_unset: bool
    """Leave unset fields out of responses (Ninja)."""
    exclude_defaults: bool
    """Leave fields equal to their default out of responses (Ninja)."""
    exclude_none: bool
    """Leave ``None`` fields out of responses (Ninja)."""
    operation_id_prefix: str
    """Prepended to every operation id (``mount(prefix=...)`` sets it for versions)."""
    url_name_prefix: str
    """Prepended (with ``_``) to explicit ``url_name`` values."""


@dataclass(frozen=True, slots=True)
class BuiltRouter:
    """What ``as_router()`` built a router from (used by system checks)."""

    controller: type[Controller]
    plugins: tuple[ControllerPlugin, ...]
    container: ContainerLike | None = None


def built_router(router: Router) -> BuiltRouter | None:
    """The controller behind ``router`` when ``as_router()`` created it."""
    return cast("BuiltRouter | None", vars(router).get("_devx_built_router"))


def built_routers() -> list[BuiltRouter]:
    """Every live router ``as_router()`` created."""
    state = runtime_state()
    return list(state.routers.values()) if state is not None else []


class Controller:
    """Base class for class-based Django Ninja controllers.

    Declare operations with ``@get``, ``@post``... on instance methods, receive
    dependencies through ``__init__`` and mount with::

        api.add_router("/users", UserController.as_router(container=container))

    Request data is never stored on ``self``; operations receive ``request`` explicitly.
    """

    scope: ClassVar[Scope] = Scope.REQUEST
    """``Scope.REQUEST``: a controller per request (inside the DI scope); ``Scope.SINGLETON``: one
    per ``as_router()`` call."""
    mode: ClassVar[Literal["sync", "async", "auto"]] = "sync"
    """Which implementation to register when an operation has an ``async_variant``;
    ``"auto"`` follows ``NINJA_DEVX["ASYNC_MODE"]``."""
    routes: ClassVar[Mapping[str, RouteOptions]] = MappingProxyType({})
    """Overrides per operation name: ``{"bulk_create": {"path": "/batch"}, "destroy":
    {"enabled": False}, "list": {"summary": "Posts"}}``. Unknown names fail at startup."""
    options: ClassVar[ControllerOptions] = ControllerOptions()
    """Merged along the MRO over ``NINJA_DEVX["DEFAULT_OPTIONS"]``, then
    ``as_router(**options)``."""

    @classmethod
    def as_router(
        cls,
        *,
        container: ContainerLike | None = None,
        scope: Scope | None = None,
        **options: Unpack[ControllerOptions],
    ) -> Router:
        """Build a new native Ninja ``Router`` exposing this controller's operations.

        Every call returns an independent router, so the same controller can be
        mounted on several APIs or prefixes with different containers and scopes.

        :param container: Builds controllers and ``Inject[...]`` values: a ``Container``, a
            dishka/svcs resolver, or any ``Resolver``/``RequestScopeProvider``.
        :param scope: Overrides the class's ``scope`` for this router.
        :param options: ``ControllerOptions`` over the class's ``options``.
        """
        operations = _collect_operations(cls)
        if not operations:
            raise ControllerConfigError(
                f"{cls.__qualname__} declares no operations; decorate methods with @get, @post..."
            )

        merged = cls.merged_options(options)
        tags = merged.get("tags")
        router = Router(
            auth=merged.get("auth", NOT_SET),
            throttle=throttle_spec(merged.get("throttle", NOT_SET)),
            tags=list(tags) if tags is not None else None,
            by_alias=merged.get("by_alias"),
            exclude_unset=merged.get("exclude_unset"),
            exclude_defaults=merged.get("exclude_defaults"),
            exclude_none=merged.get("exclude_none"),
        )
        instances = instance_provider(cls, container, Scope(scope or cls.scope))
        typevars = type_arguments(cls)
        plugins = (*get_settings().plugins, *merged.get("plugins", ()))

        registrations: list[Registration] = []
        routes = cls.routes
        if unknown := sorted(set(routes) - {name for name, _ in operations}):
            names = [name for name, _ in operations]
            raise ControllerConfigError(
                f"{cls.__qualname__}.routes names unknown operations {unknown}; "
                f"available: {', '.join(names)}.{did_you_mean(unknown[0], names)}"
            )
        for name, func in operations:
            override = routes.get(name, {})
            if override.get("enabled", True) is False:
                continue
            implementation = cls.implementation(name, func)
            for index, declared in enumerate(get_operation_specs(func)):
                spec = _apply_route(declared, override)
                spec = cls.customize_operation(name, spec)
                for plugin in plugins:
                    spec = plugin.on_operation(cls, name, spec)
                registrations.append(
                    Registration(
                        cls,
                        name,
                        index,
                        implementation,
                        spec,
                        merged,
                        instances,
                        typevars,
                        base=Controller,
                        plugins=plugins,
                    )
                )
        if not merged.get("allow_mixed_path", False):
            _warn_mixed_paths(cls, registrations)
        # Static segments must match before parameters (``/bulk`` before ``/{pk}``);
        # the sort is stable, so declaration order decides everything else.
        for registration in sorted(registrations, key=lambda r: path_specificity(r.spec.path)):
            registration.register(router)
        _install_middleware(router, merged, registrations)
        metadata = BuiltRouter(cls, plugins, container)
        vars(router)["_devx_built_router"] = metadata
        if (state := runtime_state()) is not None:
            state.routers[router] = metadata
        return router

    @classmethod
    def checks(cls, container: ContainerLike | None = None) -> list[CheckMessage]:
        """Django system check messages for this controller (``manage.py check``).

        ``container`` is the one it was mounted with. Override to add project rules;
        extend ``super().checks(container)`` to keep the built-in ones.
        """
        return []

    @classmethod
    def resolved_mode(cls) -> Literal["sync", "async"]:
        return get_settings().async_mode if cls.mode == "auto" else cls.mode

    @classmethod
    def implementation(cls, name: str, func: MethodFunction) -> MethodFunction:
        """The method registered for operation ``name``: its async variant in async mode."""
        variant: str | None = getattr(func, ASYNC_VARIANT_ATTR, None)
        if variant is None or cls.resolved_mode() == "sync":
            return func
        async_func: object = getattr(cls, variant)
        if not callable(async_func):  # pragma: no cover - set by async_variant()
            raise ControllerConfigError(f"{cls.__qualname__}.{variant} is not a method")
        return async_func

    @classmethod
    def merged_options(cls, overrides: ControllerOptions | None = None) -> ControllerOptions:
        merged: ControllerOptions = {**get_settings().default_options}
        for klass in reversed(cls.__mro__):
            declared: ControllerOptions | None = vars(klass).get("options")
            if declared is not None:
                merged.update(declared)
        if overrides:
            merged.update(overrides)
        return merged

    # --- Extension points ------------------------------------------------------------

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        """Adjust an operation before registration; ``name`` is the method name."""
        return spec

    @classmethod
    def operation_bindings(cls, name: str, spec: OperationSpec) -> Sequence[ParameterBinding]:
        """Extra hidden parameters for an operation (nested resources use this)."""
        return ()

    @classmethod
    def documented_errors(cls, name: str, spec: OperationSpec) -> frozenset[int]:
        """Extra error status codes to document for an operation."""
        return frozenset()

    def before_operation(self, request: HttpRequest, operation: OperationInfo) -> object:
        """Called after permissions and bindings, right before the method. May be ``async``."""
        return None

    def after_operation(
        self, request: HttpRequest, operation: OperationInfo, result: object
    ) -> object:
        """Called with the method's result; the return value is sent. May be ``async``."""
        return result

    def authorize_replay(
        self, request: HttpRequest, operation: OperationInfo, arguments: Mapping[str, object]
    ) -> object:
        """Authorize every keyed attempt, including retries, after bindings and preflight.

        Move authorization performed inside a custom handler here (or to bindings or
        ``before_operation``). May be async. Model controllers also recheck the URL object.
        """
        return None

    # --- Helpers ---------------------------------------------------------------------

    def resolve(self, request: HttpRequest, key: Callable[..., T]) -> T:
        """Resolve ``key`` from this call's DI scope (e.g. a service chosen at runtime)."""
        return resolve(request, key)

    async def run_sync(self, function: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Run sync (ORM) code from an async operation in one thread hop."""
        run: Callable[P, Awaitable[R]] = sync_to_async(function, thread_sensitive=True)
        return await run(*args, **kwargs)

    async def run_atomic(self, function: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Run sync code in ``transaction.atomic()`` from an async operation, in one hop."""

        def atomic() -> R:
            with transaction.atomic():
                return function(*args, **kwargs)

        run: Callable[[], Awaitable[R]] = sync_to_async(atomic, thread_sensitive=True)
        return await run()

    def check_object_permissions(self, request: HttpRequest, obj: object) -> None:
        """Enforce the current operation's object-level permissions on ``obj``."""
        check_object_permissions(request, obj)

    async def acheck_object_permissions(self, request: HttpRequest, obj: object) -> None:
        await acheck_object_permissions(request, obj)


# --- Discovery -------------------------------------------------------------------


def _install_middleware(
    router: Router, options: ControllerOptions, registrations: Sequence[Registration]
) -> None:
    from ..http.middleware import RateLimitHeadersMiddleware, use_middleware
    from ..http.throttling import RateThrottle

    middleware = list(options.get("middleware", ()))
    throttles: list[object] = []
    for source in (options, *(registration.spec.options for registration in registrations)):
        declared: object = source.get("throttle")
        if isinstance(declared, Sequence):
            throttles.extend(cast("Sequence[object]", declared))
        elif declared is not None:
            throttles.append(declared)
    wants_headers = any(isinstance(throttle, RateThrottle) for throttle in throttles)
    if wants_headers and not any(isinstance(m, RateLimitHeadersMiddleware) for m in middleware):
        middleware.append(RateLimitHeadersMiddleware())
    if middleware:
        use_middleware(router, *middleware)


def _apply_route(spec: OperationSpec, override: RouteOptions) -> OperationSpec:
    if not override:
        return spec
    options: dict[str, object] = {
        key: value for key, value in override.items() if key not in ("path", "enabled")
    }
    path = override.get("path", spec.path)
    return replace(spec, path=path, options=cast("OperationOptions", {**spec.options, **options}))


def _collect_operations(cls: type[Controller]) -> list[tuple[str, MethodFunction]]:
    """Return decorated methods in declaration order, honouring overrides.

    Route order matters for path matching: bases come before subclasses and are
    visited left to right, an override keeps its first position, and an
    undecorated override removes the route.
    """
    names: dict[str, None] = {}
    visited: set[type[object]] = set()

    def visit(klass: type[object]) -> None:
        if klass in visited:
            return
        visited.add(klass)
        for base in klass.__bases__:
            visit(base)
        names.update(dict.fromkeys(vars(klass)))

    visit(cls)
    members = {
        name: next(vars(klass)[name] for klass in cls.__mro__ if name in vars(klass))
        for name in names
    }

    operations: list[tuple[str, MethodFunction]] = []
    for name, member in members.items():
        # Look through descriptors so decorated static/class methods and properties are caught.
        target: object = (
            member.fget if isinstance(member, property) else getattr(member, "__func__", member)
        )
        if not get_operation_specs(target):
            continue
        if target is not member or not inspect.isfunction(member):
            raise ControllerConfigError(
                f"{cls.__qualname__}.{name}: operations must be plain instance methods, "
                f"not {type(member).__name__}"
            )
        operations.append((name, member))
    return operations


def _warn_mixed_paths(cls: type[Controller], registrations: Sequence[Registration]) -> None:
    """Ninja runs a path's sync operations in a thread when any of its operations is async."""
    modes: dict[str, set[bool]] = {}
    for registration in registrations:
        modes.setdefault(registration.spec.path, set()).add(registration.is_async)
    for path, kinds in modes.items():
        if len(kinds) > 1:
            warnings.warn(
                f"{cls.__qualname__}: {path!r} mixes sync and async operations, so Ninja runs "
                "the sync ones in a thread; make them all sync or all async, or pass "
                "allow_mixed_path=True",
                MixedPathWarning,
                stacklevel=3,
            )
