"""Semantic OpenAPI diff for CI: what changed, and what breaks clients.

``devx_openapi --against baseline.json`` fails on breaking changes. Additive changes
(new paths, new optional fields, new enum values) are reported but do not fail.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, cast

from ..codegen.openapi import Schema, as_mapping

__all__ = ["ADDITIVE", "BREAKING", "Change", "diff", "has_breaking"]

BREAKING: Final = "breaking"
ADDITIVE: Final = "additive"
_METHODS: Final = ("get", "put", "post", "delete", "patch", "head", "options", "query")


@dataclass(frozen=True, slots=True)
class Change:
    """One difference between two documents: ``severity`` is ``BREAKING`` or ``ADDITIVE``."""

    severity: str
    location: str
    detail: str

    def render(self) -> str:
        return f"[{self.severity}] {self.location}: {self.detail}"


def _resolve(root: Schema, value: object) -> tuple[str, Schema]:
    """The schema ``value`` points at and the last ``$ref`` followed (``""`` when inline)."""
    mapping = as_mapping(value)
    reference = ""
    seen: set[str] = set()
    while "$ref" in mapping:
        candidate = str(mapping["$ref"])
        if candidate in seen or not candidate.startswith("#/components/schemas/"):
            break
        seen.add(candidate)
        reference = candidate
        schemas = as_mapping(as_mapping(root.get("components")).get("schemas"))
        mapping = as_mapping(schemas.get(candidate.rsplit("/", 1)[-1]))
    return reference, mapping


def _required(schema: Schema) -> set[str]:
    required = schema.get("required")
    if isinstance(required, list):
        return {str(item) for item in cast("list[object]", required)}
    return set()


def _plain(schema: Schema) -> tuple[object, tuple[object, ...]]:
    kind = schema.get("type")
    enum = schema.get("enum")
    return kind, tuple(cast("list[object]", enum)) if isinstance(enum, list) else ()


def _compare_schema(
    baseline: Schema,
    current: Schema,
    *,
    root_b: Schema,
    root_c: Schema,
    location: str,
    changes: list[Change],
    visited: set[tuple[str, str]] | None = None,
) -> None:
    b_ref, baseline = _resolve(root_b, baseline)
    c_ref, current = _resolve(root_c, current)
    visited = set() if visited is None else visited
    pair = (b_ref, c_ref)
    if pair in visited:
        # The schema refers to itself; its fields were already compared higher up.
        return
    if b_ref or c_ref:
        visited.add(pair)
    _compare_resolved(
        baseline,
        current,
        root_b=root_b,
        root_c=root_c,
        location=location,
        changes=changes,
        visited=visited,
    )
    visited.discard(pair)


def _compare_resolved(
    baseline: Schema,
    current: Schema,
    *,
    root_b: Schema,
    root_c: Schema,
    location: str,
    changes: list[Change],
    visited: set[tuple[str, str]],
) -> None:
    b_kind, b_enum = _plain(baseline)
    c_kind, c_enum = _plain(current)
    if b_kind is not None and c_kind is not None and b_kind != c_kind:
        changes.append(Change(BREAKING, location, f"type changed {b_kind!r} -> {c_kind!r}"))
    if b_enum and not set(b_enum) <= set(c_enum):
        removed = sorted(str(item) for item in set(b_enum) - set(c_enum))
        changes.append(Change(BREAKING, location, f"enum values removed: {removed}"))
    if c_enum and not set(c_enum) <= set(b_enum):
        added = sorted(str(item) for item in set(c_enum) - set(b_enum))
        changes.append(Change(ADDITIVE, location, f"enum values added: {added}"))

    b_items = as_mapping(baseline.get("items"))
    c_items = as_mapping(current.get("items"))
    if b_items and c_items:
        _compare_schema(
            b_items,
            c_items,
            root_b=root_b,
            root_c=root_c,
            location=f"{location}[]",
            changes=changes,
            visited=visited,
        )

    b_props = as_mapping(baseline.get("properties"))
    c_props = as_mapping(current.get("properties"))
    b_required = _required(baseline)
    c_required = _required(current)
    for name in b_props:
        if name not in c_props:
            changes.append(Change(BREAKING, f"{location}.{name}", "field removed"))
            continue
        nested = f"{location}.{name}"
        if name in b_required and name not in c_required:
            changes.append(Change(ADDITIVE, nested, "field became optional"))
        elif name not in b_required and name in c_required:
            changes.append(Change(BREAKING, nested, "field became required"))
        _compare_schema(
            as_mapping(b_props[name]),
            as_mapping(c_props[name]),
            root_b=root_b,
            root_c=root_c,
            location=nested,
            changes=changes,
            visited=visited,
        )
    for name in c_props:
        if name not in b_props:
            severity = BREAKING if name in c_required else ADDITIVE
            changes.append(Change(severity, f"{location}.{name}", "field added"))


def _parameters(operation: Schema) -> dict[tuple[str, str], Schema]:
    found: dict[tuple[str, str], Schema] = {}
    for raw in cast("list[object]", operation.get("parameters") or []):
        parameter = as_mapping(raw)
        found[(str(parameter.get("in")), str(parameter.get("name")))] = parameter
    return found


def _body_schema(operation: Schema) -> Schema | None:
    body = as_mapping(operation.get("requestBody"))
    if not body:
        return None
    content = as_mapping(body.get("content"))
    for media, representation in content.items():
        if media.split(";", 1)[0].lower().endswith("json"):
            return as_mapping(as_mapping(representation).get("schema"))
    return None


def _responses(operation: Schema) -> dict[str, Schema]:
    found: dict[str, Schema] = {}
    for status, raw in as_mapping(operation.get("responses")).items():
        content = as_mapping(as_mapping(raw).get("content"))
        for media, representation in content.items():
            if media.split(";", 1)[0].lower().endswith("json"):
                found[status] = as_mapping(as_mapping(representation).get("schema"))
                break
    return found


def diff(baseline: Schema, current: Schema) -> list[Change]:
    """Compare two OpenAPI documents, reporting breaking and additive changes."""
    changes: list[Change] = []
    b_paths = as_mapping(baseline.get("paths"))
    c_paths = as_mapping(current.get("paths"))
    for path, raw in b_paths.items():
        if path not in c_paths:
            changes.append(Change(BREAKING, path, "path removed"))
            continue
        b_item = as_mapping(raw)
        c_item = as_mapping(c_paths[path])
        for method in _METHODS:
            if method not in b_item:
                continue
            location = f"{method.upper()} {path}"
            if method not in c_item:
                changes.append(Change(BREAKING, location, "operation removed"))
                continue
            b_op = as_mapping(b_item[method])
            c_op = as_mapping(c_item[method])
            _diff_operation(baseline, current, b_op, c_op, location, changes)
    for path, raw in c_paths.items():
        if path not in b_paths:
            changes.append(Change(ADDITIVE, path, "path added"))
            continue
        c_item = as_mapping(raw)
        for method in _METHODS:
            if method in c_item and method not in as_mapping(b_paths[path]):
                changes.append(Change(ADDITIVE, f"{method.upper()} {path}", "operation added"))
    return changes


def _diff_operation(
    baseline: Schema,
    current: Schema,
    b_op: Schema,
    c_op: Schema,
    location: str,
    changes: list[Change],
) -> None:
    b_params = _parameters(b_op)
    c_params = _parameters(c_op)
    for key, parameter in b_params.items():
        name = key[1]
        if key not in c_params:
            changes.append(Change(BREAKING, location, f"parameter {name!r} removed"))
        elif not parameter.get("required") and c_params[key].get("required"):
            changes.append(Change(BREAKING, location, f"parameter {name!r} became required"))
        elif parameter.get("required") and not c_params[key].get("required"):
            changes.append(Change(ADDITIVE, location, f"parameter {name!r} became optional"))
    for key in c_params:
        if key not in b_params and c_params[key].get("required"):
            changes.append(Change(BREAKING, location, f"required parameter {key[1]!r} added"))

    b_body = _body_schema(b_op)
    c_body = _body_schema(c_op)
    if b_body is not None and c_body is None:
        changes.append(Change(BREAKING, location, "request body removed"))
    elif b_body is not None and c_body is not None:
        _compare_schema(
            b_body,
            c_body,
            root_b=baseline,
            root_c=current,
            location=f"{location} body",
            changes=changes,
        )

    b_responses = _responses(b_op)
    c_responses = _responses(c_op)
    for status, schema in b_responses.items():
        if status not in c_responses:
            changes.append(Change(BREAKING, location, f"response {status} removed"))
        else:
            _compare_schema(
                schema,
                c_responses[status],
                root_b=baseline,
                root_c=current,
                location=f"{location} {status}",
                changes=changes,
            )


def has_breaking(changes: Iterable[Change]) -> bool:
    """Whether any change breaks existing clients."""
    return any(change.severity == BREAKING for change in changes)
