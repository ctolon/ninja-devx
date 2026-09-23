"""Resolve a mounted controller into its effective policy, for humans and tooling.

``devx_inspect`` prints the tree; ``inspect_target`` returns the same data as dataclasses so
it can be rendered as JSON or asserted in tests. Everything is read from the live controller
and its settings, never from a static declaration.
"""

from __future__ import annotations

import inspect as _inspect
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from django.utils.module_loading import import_string

from .._internal.types import MethodFunction
from ..configuration.checks import load_urlconf
from ..configuration.settings import get_settings
from ..crud.optimization import query_plan
from ..idempotency.policy import Policy as IdempotencyPolicy
from ..routing.controller import BuiltRouter, Controller, built_router, built_routers
from ..routing.operations import OperationSpec, get_operation_specs

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ninja import Router

__all__ = [
    "ControllerInspection",
    "OperationInspection",
    "as_dict",
    "inspect_target",
    "render",
    "resolve",
]


@dataclass(frozen=True, slots=True)
class OperationInspection:
    """One registered operation of a controller."""

    name: str
    methods: tuple[str, ...]
    path: str
    asynchronous: bool
    permissions: tuple[str, ...] = ()
    atomic: bool | Literal["durable"] = False
    database: str | None = None
    idempotent: bool = False
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ControllerInspection:
    """The resolved configuration and operations of one mounted controller."""

    controller: str
    prefix: str
    model: str | None = None
    scope: str = "request"
    mode: str = "sync"
    tenant: str | None = None
    owner: str | None = None
    parent: str | None = None
    object_permissions: str | None = None
    permissions: tuple[str, ...] = ()
    pagination: str | None = None
    transaction: str | None = None
    hooks: tuple[str, ...] = ()
    select: tuple[str, ...] = ()
    prefetch: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    operations: tuple[OperationInspection, ...] = field(default_factory=tuple)


def _name(value: object) -> str:
    return type(value).__name__ if not isinstance(value, type) else value.__name__


def _permissions(values: object) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(_name(item) for item in cast("Iterable[object]", values))


def _errors(errors: object) -> tuple[str, ...]:
    rules: Iterable[object] = getattr(errors, "_rules", ()) or ()
    return tuple(
        f"{getattr(getattr(rule, 'exception', None), '__name__', '?')} -> "
        f"{getattr(rule, 'status', '?')} ({getattr(rule, 'code', '?')})"
        for rule in rules
    )


def _mounted() -> list[tuple[str, BuiltRouter]]:
    """``(prefix, BuiltRouter)`` for every mounted controller, APIs from ``CHECK_APIS`` first."""
    load_urlconf()
    apis = [import_string(path) for path in get_settings().check_apis]
    if not apis:
        return [("", entry) for entry in built_routers()]
    found: list[tuple[str, BuiltRouter]] = []
    for api in apis:
        pending: list[tuple[str, object]] = list(getattr(api, "_routers", ()))
        seen: set[int] = set()
        while pending:
            prefix, router = pending.pop()
            if id(router) in seen:
                continue
            seen.add(id(router))
            if (entry := built_router(cast("Router", router))) is not None:
                found.append((prefix, entry))
            for child in getattr(router, "_routers", ()):
                pending.append((f"{prefix}{child[0]}", child[1]))
    return found


def resolve(target: str | None) -> list[tuple[str, BuiltRouter]]:
    """Mounted controllers matching ``target`` (a controller path or a route prefix).

    :raises LookupError: ``target`` is a route prefix but no ``CHECK_APIS`` are configured,
        so prefixes are unknown.
    """
    mounted = _mounted()
    if target is None:
        return mounted
    if "." in target and not target.startswith("/"):
        controller = import_string(target)
        return [(prefix, entry) for prefix, entry in mounted if entry.controller is controller]
    if not get_settings().check_apis:
        raise LookupError(
            "Route prefixes are resolved through NINJA_DEVX['CHECK_APIS']; configure it or "
            "pass a controller path"
        )
    wanted = target.rstrip("/") or "/"
    return [
        (prefix, entry)
        for prefix, entry in mounted
        if (prefix or "/").rstrip("/") == wanted or (prefix or "/").startswith(f"{wanted}/")
    ]


def _operations(controller: type[Controller]) -> tuple[OperationInspection, ...]:
    methods: list[tuple[int, str, MethodFunction]] = []
    for name, attribute in _inspect.getmembers(controller, predicate=callable):
        specs = get_operation_specs(attribute)
        if specs:
            line = getattr(getattr(attribute, "__code__", None), "co_firstlineno", 0)
            methods.append((line, name, attribute))
    found: list[OperationInspection] = []
    for _, name, attribute in sorted(methods, key=lambda item: item[0]):
        implementation = controller.implementation(name, attribute)
        asynchronous = _inspect.iscoroutinefunction(implementation) or _inspect.isasyncgenfunction(
            implementation
        )
        for spec in get_operation_specs(attribute):
            override: Mapping[str, object] = controller.routes.get(name, {})
            if override.get("enabled") is False:
                continue
            options: dict[str, object] = {
                **spec.options,
                **{k: v for k, v in override.items() if k not in {"path", "enabled"}},
            }
            found.append(_operation(name, spec, override, options, asynchronous))
    return tuple(found)


def _operation(
    name: str,
    spec: OperationSpec,
    override: Mapping[str, object],
    options: Mapping[str, object],
    asynchronous: bool,
) -> OperationInspection:
    decorators = cast("Sequence[object]", options.get("decorators") or ())
    database = options.get("database")
    atomic = options.get("atomic", False)
    return OperationInspection(
        name=name,
        methods=spec.methods,
        path=str(override.get("path", spec.path)),
        asynchronous=asynchronous,
        permissions=_permissions(options.get("permissions")),
        atomic="durable" if atomic == "durable" else bool(atomic),
        database=database if isinstance(database, str) else None,
        idempotent=any(isinstance(item, IdempotencyPolicy) for item in decorators),
        errors=_errors(options.get("errors")),
    )


def inspect_target(target: str | None = None) -> list[ControllerInspection]:
    """Inspect every mounted controller matching ``target`` (all of the APIs without one)."""
    results: list[ControllerInspection] = []
    for prefix, entry in resolve(target):
        results.append(_inspect_controller(prefix, entry))
    return results


def _inspect_controller(prefix: str, entry: BuiltRouter) -> ControllerInspection:
    controller = entry.controller
    options: Mapping[str, object] = controller.merged_options()
    model = getattr(controller, "get_model", lambda: None)()
    schema = getattr(controller, "output_schema", lambda: None)()
    select: tuple[str, ...] = ()
    prefetch: tuple[str, ...] = ()
    if model is not None and schema is not None:
        hints: Sequence[str] = getattr(controller, "related", ())
        select, prefetch = query_plan(model, schema, hints=hints).lookups()
    atomic = options.get("atomic", False)
    database = options.get("database")
    transaction = None
    if atomic:
        transaction = "durable" if atomic == "durable" else "atomic"
        if isinstance(database, str):
            transaction = f"{transaction} ({database})"
    parent = getattr(controller, "parent", None)
    object_permissions = getattr(controller, "object_permissions", None)
    hooks = cast("Iterable[object]", options.get("hooks") or ())
    return ControllerInspection(
        controller=controller.__qualname__,
        prefix=prefix or "/",
        model=model.__name__ if model is not None else None,
        scope=getattr(controller.scope, "name", str(controller.scope)).lower(),
        mode=controller.resolved_mode(),
        tenant=getattr(controller, "tenant_field", None),
        owner=getattr(controller, "owner_field", None),
        parent=type(parent).__name__ if parent is not None else None,
        object_permissions=_name(object_permissions) if object_permissions is not None else None,
        permissions=_permissions(options.get("permissions")),
        pagination=getattr(getattr(controller, "pagination_class", None), "__name__", None),
        transaction=transaction,
        hooks=tuple(_name(hook) for hook in hooks),
        select=select,
        prefetch=prefetch,
        errors=_errors(options.get("errors")),
        operations=_operations(controller),
    )


def as_dict(results: Iterable[ControllerInspection]) -> list[dict[str, object]]:
    """The inspections as plain dictionaries, ready for ``json.dumps``."""
    return [asdict(item) for item in results]


def render(inspection: ControllerInspection) -> str:
    """A box-drawing tree of one controller inspection."""
    lines = [f"{inspection.controller}  ({inspection.prefix})"]
    fields: list[tuple[str, object]] = [
        ("model", inspection.model),
        ("scope", inspection.scope),
        ("mode", inspection.mode),
        ("tenant", inspection.tenant),
        ("owner", inspection.owner),
        ("parent", inspection.parent),
        ("object permissions", inspection.object_permissions),
        ("permissions", ", ".join(inspection.permissions)),
        ("pagination", inspection.pagination),
        ("transaction", inspection.transaction),
        ("hooks", ", ".join(inspection.hooks)),
        ("errors", ", ".join(inspection.errors)),
    ]
    entries: list[tuple[str, list[str]]] = [
        (label, [str(value)]) for label, value in fields if value
    ]
    relations: list[str] = []
    if inspection.select:
        relations.append(f"select_related: {', '.join(inspection.select)}")
    if inspection.prefetch:
        relations.append(f"prefetch_related: {', '.join(inspection.prefetch)}")
    if relations:
        entries.append(("relations", relations))
    operations: list[str] = []
    for operation in inspection.operations:
        method = "/".join(operation.methods)
        detail = [f"{method} {operation.path}  {operation.name}"]
        if operation.asynchronous:
            detail.append("async")
        if operation.permissions:
            detail.append(f"permissions: {', '.join(operation.permissions)}")
        if operation.atomic:
            detail.append(f"atomic: {operation.atomic}")
        if operation.idempotent:
            detail.append("idempotent")
        if operation.errors:
            detail.append(f"errors: {', '.join(operation.errors)}")
        operations.append(" | ".join(detail))
    entries.append(("operations", operations))
    for index, (label, children) in enumerate(entries):
        last = index == len(entries) - 1
        connector = "└──" if last else "├──"
        indent = "    " if last else "│   "
        if len(children) == 1 and label != "operations":
            lines.append(f"{connector} {label}: {children[0]}")
            continue
        lines.append(f"{connector} {label}")
        for child_index, child in enumerate(children):
            marker = "└──" if child_index == len(children) - 1 else "├──"
            lines.append(f"{indent}{marker} {child}")
    return "\n".join(lines)
