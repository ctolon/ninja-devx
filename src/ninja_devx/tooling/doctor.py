"""Findings beyond the system checks, across every mounted controller.

``devx_doctor`` inspects the same mounted controllers as ``manage.py check``
(:mod:`ninja_devx.configuration.checks`), through :func:`ninja_devx.tooling.inspect.resolve`,
but looks for things that are valid yet risky rather than broken: a filter, search or
ordering field with no database index, an ``owner_field``/``tenant_field`` with nothing
actually enforcing it, an output field the N+1 planner cannot resolve statically, a list
endpoint with no pagination, a controller with no permission at all, and a soft-deleted
model whose unique fields are not scoped to active rows.

Each check is one small function added to :data:`CHECKS`, so adding a new one is one
function::

    @_register
    def _check_something(target: _Target) -> Iterator[Finding]:
        if ...:
            yield Finding("warn", target.inspection.controller, "message", "fix hint")

:func:`run_doctor` runs every registered check over every controller ``resolve(target)``
finds.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

from django.db.models import Field, Model, UniqueConstraint
from pydantic import BaseModel

from ..configuration.settings import get_settings
from ..crud.fields import resolve_field
from ..exceptions import ControllerConfigError
from ..layers.persistence import model_field
from .inspect import ControllerInspection, inspect_target, resolve

__all__ = ["CHECKS", "Finding", "Severity", "run_doctor"]

Severity = Literal["info", "warn"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing ``devx_doctor`` noticed about a mounted controller."""

    severity: Severity
    """``"info"`` or ``"warn"``."""
    controller: str
    """Controller name, from :attr:`ControllerInspection.controller`."""
    message: str
    """What was noticed."""
    hint: str
    """How to address it."""

    def __str__(self) -> str:
        return f"{self.severity:<4}  {self.controller}  {self.message} ({self.hint})"


@dataclass(frozen=True, slots=True)
class _Target:
    """One mounted controller, with its model and output schema resolved (if it has them)."""

    inspection: ControllerInspection
    controller: type[object]
    model: type[Model] | None
    schema: type[BaseModel] | None


Check = Callable[[_Target], Iterable[Finding]]
CHECKS: Final[list[Check]] = []


def _register(check: Check) -> Check:
    CHECKS.append(check)
    return check


def run_doctor(target: str | None = None) -> list[Finding]:
    """Run every registered check over every controller ``resolve(target)`` finds.

    :param target: A controller path or a mounted route prefix; every controller of the
        configured APIs when ``None`` (see :func:`ninja_devx.tooling.inspect.resolve`).
    """
    findings: list[Finding] = []
    for inspection, (_, entry) in zip(inspect_target(target), resolve(target), strict=True):
        controller = entry.controller
        model = getattr(controller, "get_model", lambda: None)()
        schema = getattr(controller, "output_schema", lambda: None)()
        current = _Target(inspection, controller, model, schema)
        for check in CHECKS:
            findings.extend(check(current))
    return findings


def _indexed(field: Field[object, object]) -> bool:
    return bool(
        field.primary_key
        or field.unique
        or getattr(field, "db_index", False)  # not in the django-stubs instance attributes
        or field.many_to_one
        or field.one_to_one
        or field.many_to_many
    )


@_register
def _check_unindexed_query_fields(target: _Target) -> Iterator[Finding]:
    """``search_fields``/``filter_fields``/``ordering_fields`` without an index."""
    controller, model = target.controller, target.model
    if model is None:
        return
    paths = dict.fromkeys(
        (
            *getattr(controller, "search_fields", ()),
            *getattr(controller, "filter_fields", ()),
            *getattr(controller, "ordering_fields", ()),
            *(name.removeprefix("-") for name in getattr(controller, "default_ordering", ())),
        )
    )
    for path in paths:
        try:
            field = resolve_field(model, path)
        except ControllerConfigError:
            continue  # ninja_devx.E002 already reports a missing field
        if isinstance(field, Field) and not _indexed(field):
            yield Finding(
                "warn",
                target.inspection.controller,
                f"{model.__name__}.{path} is filtered, searched or ordered on without a "
                "database index",
                f"Add db_index=True (or unique=True) to {model.__name__}.{path}, or accept "
                "a full-table scan on a large table.",
            )


@_register
def _check_owner_without_isowner(target: _Target) -> Iterator[Finding]:
    """``owner_field`` set, but ``IsOwner`` is not enforced and object permissions are off."""
    inspection = target.inspection
    if not inspection.owner or inspection.object_permissions:
        return
    if "IsOwner" not in inspection.permissions:
        yield Finding(
            "warn",
            inspection.controller,
            f"owner_field {inspection.owner!r} is set, but IsOwner is not enforced and "
            "object_permissions is not configured",
            f"Add IsOwner({inspection.owner!r}) to permissions, or set object_permissions "
            "(ModelController adds IsOwner automatically unless merged_options() was "
            "overridden without calling super()).",
        )


@_register
def _check_tenant_without_resolver(target: _Target) -> Iterator[Finding]:
    """``tenant_field`` set, but nothing resolves the tenant except ``request.tenant``."""
    inspection = target.inspection
    if not inspection.tenant:
        return
    controller = target.controller
    settings = get_settings()
    configured = (
        getattr(controller, "tenant_resolver", None) is not None
        or getattr(controller, "tenant_context", None) is not None
        or settings.tenant_resolver is not None
        or settings.tenant_context is not None
    )
    if configured:
        return
    yield Finding(
        "info",
        inspection.controller,
        f"tenant_field {inspection.tenant!r} is set, but no tenant_resolver/tenant_context "
        "is configured on the controller or NINJA_DEVX",
        "Requests fall back to request.tenant; set tenant_resolver=, tenant_context=, or "
        "NINJA_DEVX['TENANT_RESOLVER'] unless middleware always sets request.tenant.",
    )


def _resolver_hints(resolver: object) -> tuple[str, ...]:
    hints: tuple[str, ...] = getattr(resolver, "__ninja_devx_related__", ())
    if not hints:
        function = getattr(resolver, "__func__", None)
        hints = getattr(function, "__ninja_devx_related__", ())
    return hints


@_register
def _check_unhinted_resolver_fields(target: _Target) -> Iterator[Finding]:
    """Output fields resolved by a method the N+1 planner cannot analyse, with no hint."""
    model, schema, controller = target.model, target.schema, target.controller
    if model is None or schema is None:
        return
    blanket = bool(getattr(controller, "related", ()))
    for name, info in schema.model_fields.items():
        attribute = info.alias if isinstance(info.alias, str) else name
        if "." in attribute or model_field(model, attribute) is not None:
            continue
        resolver = getattr(schema, f"resolve_{name}", None)
        if resolver is None or blanket or _resolver_hints(resolver):
            continue
        yield Finding(
            "warn",
            target.inspection.controller,
            f"{schema.__name__}.{name} is filled by resolve_{name}(), which the N+1 planner "
            "cannot analyse, and no related hint covers it",
            f'Add related = ("<relation>",) to the controller, or '
            f'@requires_related("<relation>") on resolve_{name} '
            "(see ninja_devx.crud.optimization).",
        )


@_register
def _check_list_without_pagination(target: _Target) -> Iterator[Finding]:
    """A list endpoint with no ``pagination_class`` configured."""
    inspection, controller = target.inspection, target.controller
    if not callable(getattr(controller, "list_queryset", None)):
        return
    if not any(operation.name == "list" for operation in inspection.operations):
        return
    if inspection.pagination:
        return
    yield Finding(
        "warn",
        inspection.controller,
        "the list endpoint has no pagination_class configured",
        "Set pagination_class (or NINJA_DEVX['PAGINATION_CLASS']); otherwise list returns "
        "every row.",
    )


@_register
def _check_no_permissions(target: _Target) -> Iterator[Finding]:
    """A controller with no permission configured at all."""
    inspection = target.inspection
    if inspection.permissions:
        return
    yield Finding(
        "warn",
        inspection.controller,
        "no permission is configured; every operation is open",
        "Add permissions=[IsAuthenticated()] (or another BasePermission) to options, "
        "unless the controller is intentionally public.",
    )


class _SoftDeleteMarker(Protocol):
    field: str


class _SoftDeleteResolved(Protocol):
    config: _SoftDeleteMarker


@_register
def _check_soft_delete_unique(target: _Target) -> Iterator[Finding]:
    """A soft-deleted model whose unique fields are not scoped to active rows."""
    controller, model = target.controller, target.model
    soft_delete_config = getattr(controller, "soft_delete_config", None)
    if model is None or not callable(soft_delete_config):
        return
    resolved = cast("_SoftDeleteResolved", soft_delete_config())
    marker = resolved.config.field
    covered = {
        frozenset(constraint.fields)
        for constraint in model._meta.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.condition is not None
    }
    for field in model._meta.get_fields():
        if not isinstance(field, Field) or field.primary_key or field.name == marker:
            continue
        if not field.unique or frozenset({field.name}) in covered:
            continue
        yield Finding(
            "warn",
            target.inspection.controller,
            f"{model.__name__}.{field.name} is globally unique, but {controller.__name__} "
            "soft-deletes rows: a deleted row's value can never be reused",
            f'Replace unique=True with soft_delete_unique({model.__name__}, "{field.name}") '
            "in the model's Meta.constraints.",
        )
