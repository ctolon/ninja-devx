"""Project-wide defaults read from ``settings.NINJA_DEVX``.

::

    NINJA_DEVX = {
        "DEFAULT_OPTIONS": ControllerOptions(auth=JWTAuth()),
        "PAGINATION_CLASS": "ninja.pagination.PageNumberPagination",
        "BULK_LIMIT": 500,
    }

A class attribute set on a user controller always wins over these settings.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, TypedDict, TypeVar, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.http import HttpRequest
from django.utils.module_loading import import_string
from ninja.pagination import PaginationBase

if TYPE_CHECKING:
    from ..http.errors import ErrorMap
    from ..routing.controller import ControllerOptions
    from ..routing.plugins import ControllerPlugin

__all__ = ["NinjaDevXSettings", "ResolvedSettings", "class_setting", "get_settings"]

T = TypeVar("T")

SETTINGS_NAME: Final = "NINJA_DEVX"
_LIBRARY_PACKAGE: Final = "ninja_devx"


class NinjaDevXSettings(TypedDict, total=False):
    DEFAULT_OPTIONS: ControllerOptions
    """Options applied below every controller's own ``options``."""
    DOCUMENT_ERRORS: bool
    """Add 401/403/404/422 responses to OpenAPI (default ``True``)."""
    VALIDATE_MODEL: bool
    """Run ``Model.full_clean()`` on CRUD writes; errors are 422."""
    REFRESH_AFTER_WRITE: bool
    """Reload objects through ``scoped_queryset()`` after writes."""
    OPTIMIZE_QUERIES: bool | Literal["only"]
    """Derive ``select_related``/``prefetch_related`` from output schemas (default ``True``)."""
    PAGINATION_CLASS: type[PaginationBase] | str | None
    """Default pagination class for CRUD lists (a class or import path)."""
    BULK_LIMIT: int
    """Maximum objects per bulk request."""
    IDEMPOTENCY_TTL: int
    """Seconds a stored ``idempotent()`` response is replayed."""
    IDEMPOTENCY_DATABASE: str
    """Database alias for durable idempotency records; must use autocommit."""
    ERRORS: ErrorMap | str
    """Project error rules (an ``ErrorMap`` or its import path) added to the defaults."""
    ERROR_FORMAT: Literal["ninja", "problem+json"]
    """Error body format: ``"ninja"`` (``{detail, code}``) or ``"problem+json"`` (RFC 9457)."""
    ASYNC_MODE: Literal["sync", "async"]
    """What ``mode = "auto"`` model controllers use (default ``"sync"``)."""
    WARN_BLOCKING_MS: float | None
    """Warn when sync code blocks an async operation's event loop longer than this."""
    ASYNC_FETCH_MODE: Literal["lazy", "raise"]
    """``"raise"`` makes lazy relation loads fail loudly in async operations (Django 6.1+)."""
    PLUGINS: Sequence[ControllerPlugin | str]
    """``ControllerPlugin`` objects (or import paths) applied to every controller."""
    TENANT_RESOLVER: Callable[[HttpRequest], object] | str
    """A function of the request returning the tenant (sync or async), or its import path."""
    TENANT_CONTEXT: object
    """A ``RequestContext[User, Tenant]`` key (or its import path) whose ``tenant`` is used."""
    THROTTLE_RATES: Mapping[str, str | None]
    """Rates for ``ScopedRateThrottle`` scopes, e.g. ``{"uploads": "10/min"}``."""
    THROTTLE_STORAGE: object
    """A ``ThrottleStorage`` (or its import path) used by throttles without their own
    ``storage=``; default: cache-based fixed windows."""
    OBJECT_PERMISSION_BACKEND: object
    """An ``ObjectPermissionBackend`` (or its import path); default: grants, guardian, Django."""
    CHECK_APIS: Sequence[str]
    """``NinjaAPI`` import paths validated by ``manage.py check``."""
    WEBHOOK_SECRET_KEYS: Sequence[str]
    """Fernet keys encrypting webhook signing secrets at rest (``ninja-devx[crypto]``); the
    first encrypts, all decrypt. Empty: secrets are stored as they are."""


@dataclass(frozen=True, slots=True)
class ResolvedSettings:
    default_options: ControllerOptions = field(
        default_factory=lambda: cast("ControllerOptions", {})
    )
    document_errors: bool = True
    validate_model: bool = True
    refresh_after_write: bool = True
    optimize_queries: bool | Literal["only"] = True
    pagination_class: type[PaginationBase] | None = None
    bulk_limit: int = 100
    idempotency_ttl: int = 60 * 60 * 24
    idempotency_database: str = "default"
    errors: ErrorMap | None = None
    error_format: Literal["ninja", "problem+json"] = "ninja"
    async_mode: Literal["sync", "async"] = "sync"
    warn_blocking_ms: float | None = None
    async_fetch_mode: Literal["lazy", "raise"] = "lazy"
    plugins: tuple[ControllerPlugin, ...] = ()
    check_apis: tuple[str, ...] = ()
    tenant_resolver: Callable[[HttpRequest], object] | None = None
    tenant_context: object | None = None
    throttle_rates: Mapping[str, str | None] = field(default_factory=dict[str, "str | None"])
    throttle_storage: object | None = None
    object_permission_backend: object | None = None
    webhook_secret_keys: tuple[str, ...] = ()


_KEYS: Final = frozenset(NinjaDevXSettings.__annotations__)


def get_settings() -> ResolvedSettings:
    from .runtime import runtime_state

    state = runtime_state()
    if state is None:
        return _resolve_settings()
    if state.settings is None:
        state.settings = _resolve_settings()
    return state.settings


def _resolve_settings() -> ResolvedSettings:
    raw: object = getattr(settings, SETTINGS_NAME, {})
    if not isinstance(raw, dict):
        raise ImproperlyConfigured(f"settings.{SETTINGS_NAME} must be a dict")
    values = cast("NinjaDevXSettings", raw)
    if unknown := sorted(set(values) - _KEYS):
        from .._internal.types import did_you_mean

        raise ImproperlyConfigured(
            f"Unknown settings.{SETTINGS_NAME} keys {unknown}; expected {sorted(_KEYS)}."
            f"{did_you_mean(unknown[0], _KEYS)}"
        )
    from ..http.errors import ErrorMap
    from ..routing.plugins import ControllerPlugin

    defaults = ResolvedSettings()
    return ResolvedSettings(
        default_options=values.get("DEFAULT_OPTIONS", defaults.default_options),
        document_errors=values.get("DOCUMENT_ERRORS", defaults.document_errors),
        validate_model=values.get("VALIDATE_MODEL", defaults.validate_model),
        refresh_after_write=values.get("REFRESH_AFTER_WRITE", defaults.refresh_after_write),
        optimize_queries=values.get("OPTIMIZE_QUERIES", defaults.optimize_queries),
        pagination_class=_pagination_class(values.get("PAGINATION_CLASS")),
        bulk_limit=values.get("BULK_LIMIT", defaults.bulk_limit),
        idempotency_ttl=values.get("IDEMPOTENCY_TTL", defaults.idempotency_ttl),
        idempotency_database=values.get("IDEMPOTENCY_DATABASE", defaults.idempotency_database),
        errors=_imported(values.get("ERRORS"), "ERRORS", ErrorMap),
        error_format=values.get("ERROR_FORMAT", defaults.error_format),
        async_mode=values.get("ASYNC_MODE", defaults.async_mode),
        warn_blocking_ms=values.get("WARN_BLOCKING_MS", defaults.warn_blocking_ms),
        async_fetch_mode=values.get("ASYNC_FETCH_MODE", defaults.async_fetch_mode),
        plugins=tuple(
            plugin
            for entry in values.get("PLUGINS", ())
            if (plugin := _imported(entry, "PLUGINS", ControllerPlugin))  # type: ignore[type-abstract]
            is not None
        ),
        check_apis=tuple(values.get("CHECK_APIS", ())),
        tenant_resolver=_callable(values.get("TENANT_RESOLVER"), "TENANT_RESOLVER"),
        tenant_context=_import(values.get("TENANT_CONTEXT")),
        throttle_rates=dict(values.get("THROTTLE_RATES", {})),
        throttle_storage=values.get("THROTTLE_STORAGE"),
        object_permission_backend=values.get("OBJECT_PERMISSION_BACKEND"),
        webhook_secret_keys=tuple(values.get("WEBHOOK_SECRET_KEYS", ())),
    )


def _callable(value: object, name: str) -> Callable[[HttpRequest], object] | None:
    value = _import(value)
    if value is None:
        return None
    if not callable(value):
        raise ImproperlyConfigured(f"{SETTINGS_NAME}[{name!r}] must be callable")
    return cast("Callable[[HttpRequest], object]", value)


def _import(value: object) -> object:
    return import_string(value) if isinstance(value, str) else value


def _imported(value: object, name: str, expected: type[T]) -> T | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = import_string(value)
    if not isinstance(value, expected):
        raise ImproperlyConfigured(f"{SETTINGS_NAME}[{name!r}] must be a {expected.__name__}")
    return value


def _pagination_class(value: type[PaginationBase] | str | None) -> type[PaginationBase] | None:
    if value is None or isinstance(value, type):
        return value
    imported: object = import_string(value)
    if not (isinstance(imported, type) and issubclass(imported, PaginationBase)):
        raise ImproperlyConfigured(f"{SETTINGS_NAME}['PAGINATION_CLASS'] is not a pagination class")
    return imported


def _clear_cache(*, setting: str, **kwargs: object) -> None:
    if setting == SETTINGS_NAME:
        from .runtime import runtime_state

        if (state := runtime_state()) is not None:
            state.settings = None


setting_changed.connect(_clear_cache)


def class_setting(cls: type[object], attribute: str, default: T) -> T:
    """``cls.<attribute>`` when a user class sets it, otherwise the project ``default``."""
    for klass in cls.__mro__:
        if attribute in vars(klass) and not klass.__module__.startswith(_LIBRARY_PACKAGE):
            value: T = vars(klass)[attribute]
            return value
    return default
