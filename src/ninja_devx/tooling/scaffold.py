"""Generate explicit, typed schemas, a controller and tests from a Django model.

Used by ``manage.py devx_scaffold``. The output is plain Python that type checkers
understand, so generated schemas can be used as generic arguments.
"""

from __future__ import annotations

import json
import keyword
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Final, Literal, get_args, get_origin
from uuid import UUID

from django.db import models
from django.db.models import Field, Model

from .._internal.compat import has_db_default
from ..crud.fields import ModelField, field_type

__all__ = ["ScaffoldOptions", "render_resource", "render_tests", "snake_case"]

_TYPE_IMPORTS: Final[Mapping[type[object], tuple[str, str]]] = MappingProxyType(
    {
        date: ("datetime", "date"),
        datetime: ("datetime", "datetime"),
        time: ("datetime", "time"),
        timedelta: ("datetime", "timedelta"),
        Decimal: ("decimal", "Decimal"),
        UUID: ("uuid", "UUID"),
    }
)
_TEXT_FIELDS: Final = (models.CharField, models.TextField)
_DATABASE_INTEGER_LIMIT: Final = 2**31 - 1
_CLIENT: Final = "Callable[..., TestClient]"


@dataclass(frozen=True, slots=True)
class ScaffoldOptions:
    read_fields: Sequence[str] | None = None
    write_fields: Sequence[str] | None = None
    owner_field: str | None = None
    asynchronous: bool = False


@dataclass(slots=True)
class _Imports:
    names: dict[str, set[str]] = field(default_factory=dict[str, set[str]])

    def add(self, module: str, name: str) -> str:
        self.names.setdefault(module, set()).add(name)
        return name

    def render(self, first_party: Sequence[str]) -> str:
        stdlib = {"datetime", "decimal", "typing", "uuid", "__future__"}
        groups: list[list[str]] = [[], [], []]
        for module in sorted(self.names, key=lambda m: (m != "__future__", m)):
            names = ", ".join(sorted(self.names[module]))
            line = f"from {module} import {names}"
            if module in stdlib:
                groups[0].append(line)
            elif module.split(".")[0] in first_party:
                groups[2].append(line)
            else:
                groups[1].append(line)
        return "\n\n".join("\n".join(group) for group in groups if group)


def snake_case(name: str) -> str:
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def render_resource(model: type[Model], options: ScaffoldOptions | None = None) -> str:
    """Schemas (``<Model>In``/``<Model>Out``) and a controller for ``model``."""
    options = options or ScaffoldOptions()
    name = model.__name__
    imports = _Imports()
    imports.add(model.__module__, name)
    imports.add("ninja", "Schema")
    base = "AsyncCRUDController" if options.asynchronous else "CRUDController"
    imports.add("ninja_devx.crud", base)

    read = _fields(model, options.read_fields, writable=False)
    write = [
        f
        for f in _fields(model, options.write_fields, writable=True)
        if f.name != options.owner_field
    ]

    out_lines = [f"class {name}Out(Schema):"]
    resolvers: list[str] = []
    for model_field in read:
        attribute, annotation = _read_annotation(model_field, imports)
        out_lines.append(f"    {attribute}: {annotation}")
        if model_field.many_to_many:
            resolvers.append(
                f"    @staticmethod\n"
                f"    def resolve_{attribute}(obj: {name}) -> {annotation}:\n"
                f"        return [related.pk for related in obj.{model_field.name}.all()]"
            )
    out_block = "\n".join(
        out_lines + (["", *"\n\n".join(resolvers).split("\n")] if resolvers else [])
    )

    in_lines = [f"class {name}In(Schema):"]
    for model_field in sorted(write, key=_required_first):
        attribute, annotation, default = _write_annotation(model_field, imports)
        in_lines.append(f"    {attribute}: {annotation}{default}")
    if len(in_lines) == 1:
        in_lines.append("    pass")

    controller = [f"class {name}Controller({base}[{name}, {name}Out, {name}In]):"]
    queryable = [f for f in read if _is_queryable(f)]
    search = [f.name for f in queryable if isinstance(f, _TEXT_FIELDS) and not f.choices][:3]
    ordering = _ordering_fields(queryable)
    filters = _filter_fields(queryable)
    if options.owner_field:
        controller.append(f'    owner_field = "{options.owner_field}"')
    if search:
        controller.append(_assignment("search_fields", [f'"{name}"' for name in search], "()"))
    if filters:
        items = [f'"{key}": {_tuple(value)}' for key, value in filters.items()]
        controller.append(_assignment("filter_fields", items, "{}"))
    if ordering:
        controller.append(_assignment("ordering_fields", [f'"{name}"' for name in ordering], "()"))
    if len(controller) == 1:
        controller.append("    pass")

    header = (
        f'"""API for {model._meta.verbose_name_plural} (generated by ``devx_scaffold``).\n\n'
        f'Mount with ``api.add_router("/{snake_case(name)}s", {name}Controller.as_router())``.\n'
        '"""'
    )
    body = "\n\n\n".join([out_block, "\n".join(in_lines), "\n".join(controller)])
    first_party = [model.__module__.split(".")[0]]
    return f"{header}\n\n{imports.render(first_party)}\n\n\n{body}\n"


def render_tests(model: type[Model], module: str, options: ScaffoldOptions | None = None) -> str:
    options = options or ScaffoldOptions()
    name = model.__name__
    snake = snake_case(name)
    lines = [
        f'"""Tests for the {name} API: generated by ``manage.py devx_scaffold``."""',
        "",
        "from collections.abc import Callable",
        "",
        "import pytest",
        "from ninja import NinjaAPI",
        "from ninja.testing import TestClient",
        "",
        f"from {module} import {name}Controller",
        "",
        "pytestmark = pytest.mark.django_db",
        "",
        "",
        f"def test_{snake}_openapi_builds() -> None:",
        "    api = NinjaAPI()",
        f'    api.add_router("/{snake}s", {name}Controller.as_router())',
        '    assert api.get_openapi_schema(path_prefix="")["paths"]',
        "",
        "",
    ]
    if options.owner_field:
        lines += [
            f"def test_{snake}_list_requires_authentication(ninja_client: {_CLIENT}) -> None:",
            f'    assert ninja_client({name}Controller).get("/").status_code == 401',
        ]
    else:
        lines += [
            f"def test_{snake}_list(ninja_client: {_CLIENT}) -> None:",
            f'    assert ninja_client({name}Controller).get("/").status_code == 200',
            "",
            "",
            f"def test_{snake}_missing_is_404(ninja_client: {_CLIENT}) -> None:",
            f"    client = ninja_client({name}Controller)",
            f'    assert client.get("/{_missing_lookup(model)}").status_code == 404',
        ]
    return "\n".join(lines) + "\n"


# --- Field selection -------------------------------------------------------------


def _fields(
    model: type[Model], names: Sequence[str] | None, *, writable: bool
) -> list[Field[object, object]]:
    candidates: list[Field[object, object]] = [
        f
        for f in model._meta.get_fields()
        if isinstance(f, Field) and (f.concrete or f.many_to_many)
    ]
    if names is not None:
        by_name = {f.name: f for f in candidates}
        unknown = sorted(set(names) - set(by_name))
        if unknown:
            raise ValueError(f"{model.__name__} has no fields {unknown}")
        return [by_name[n] for n in names]
    if writable:
        return [f for f in candidates if _is_writable(f)]
    return candidates


def _is_writable(model_field: Field[object, object]) -> bool:
    if model_field.primary_key or not model_field.editable:
        return False
    automatic = getattr(model_field, "auto_now", False) or getattr(
        model_field, "auto_now_add", False
    )
    return not automatic


def _is_queryable(model_field: Field[object, object]) -> bool:
    """Fields worth offering for search, filters and ordering (not internal bookkeeping)."""
    automatic = getattr(model_field, "auto_now", False) or getattr(
        model_field, "auto_now_add", False
    )
    return model_field.editable or model_field.primary_key or automatic


def _assignment(name: str, items: Sequence[str], brackets: Literal["()", "{}"]) -> str:
    """``name = (a, b)`` on one line, or one item per line when it exceeds 100 characters."""
    opening, closing = brackets[0], brackets[1]
    if len(items) == 1 and opening == "(":
        single = f"    {name} = ({items[0]},)"
        if len(single) <= 100:
            return single
    line = f"    {name} = {opening}{', '.join(items)}{closing}"
    if len(line) <= 100:
        return line
    body = "".join(f"        {item},\n" for item in items)
    return f"    {name} = {opening}\n{body}    {closing}"


def _required_first(model_field: Field[object, object]) -> int:
    return 0 if _is_required(model_field) else 1


def _is_required(model_field: Field[object, object]) -> bool:
    if model_field.many_to_many:
        return False
    return not (
        model_field.null
        or model_field.blank
        or model_field.has_default()
        or has_db_default(model_field)
    )


# --- Annotations -----------------------------------------------------------------


def _type_text(python_type: object, imports: _Imports) -> str:
    if get_origin(python_type) is not None:  # Literal[...] from choices
        imports.add("typing", "Literal")
        return f"Literal[{', '.join(_literal(value) for value in get_args(python_type))}]"
    if isinstance(python_type, type):
        if python_type in _TYPE_IMPORTS:
            module, name = _TYPE_IMPORTS[python_type]
            return imports.add(module, name)
        return python_type.__name__
    return "str"


def _attribute(model_field: Field[object, object]) -> str:
    name = (
        model_field.attname
        if model_field.many_to_one or model_field.one_to_one
        else model_field.name
    )
    return f"{name}_" if keyword.iskeyword(name) else name


def _read_annotation(
    model_field: Field[object, object], imports: _Imports, *, writing: bool = False
) -> tuple[str, str]:
    if isinstance(model_field, models.JSONField):
        text = imports.add("pydantic", "JsonValue")
    else:
        text = _type_text(field_type(_as_model_field(model_field), choices=True), imports)
    if model_field.many_to_many:
        return model_field.name, _constrained(f"list[{text}]", model_field, imports, writing)
    annotation = f"{text} | None" if model_field.null else text
    return _attribute(model_field), _constrained(annotation, model_field, imports, writing)


def _constrained(
    annotation: str, model_field: Field[object, object], imports: _Imports, writing: bool
) -> str:
    """``Annotated[T, Field(...)]`` carrying the model's constraints and help text."""
    constraints = _constraints(model_field) if writing else {}
    help_text = str(model_field.help_text or "")
    if help_text:
        constraints["description"] = help_text
    if not constraints:
        return annotation
    imports.add("typing", "Annotated")
    field = imports.add("pydantic", "Field")
    arguments = ", ".join(f"{key}={_literal(value)}" for key, value in constraints.items())
    return f"Annotated[{annotation}, {field}({arguments})]"


def _constraints(model_field: Field[object, object]) -> dict[str, object]:
    """Validation the database or ``full_clean`` would apply, moved to the API edge."""
    from django.core import validators

    found: dict[str, object] = {}
    if model_field.choices or model_field.many_to_many or model_field.is_relation:
        return found
    if isinstance(model_field, _TEXT_FIELDS):
        max_length = getattr(model_field, "max_length", None)
        if isinstance(max_length, int):
            found["max_length"] = max_length
        if not model_field.blank:
            found["min_length"] = 1
    if isinstance(
        model_field,
        models.PositiveIntegerField
        | models.PositiveSmallIntegerField
        | models.PositiveBigIntegerField,
    ):
        found["ge"] = 0
    if isinstance(model_field, models.DecimalField):
        found["max_digits"] = model_field.max_digits
        found["decimal_places"] = model_field.decimal_places
    for validator in model_field.validators:
        limit: object = getattr(validator, "limit_value", None)
        if isinstance(limit, int) and abs(limit) >= _DATABASE_INTEGER_LIMIT:
            continue  # the database backend's integer range, not a business rule
        if isinstance(validator, validators.MinValueValidator):
            found["ge"] = limit
        elif isinstance(validator, validators.MaxValueValidator):
            found["le"] = limit
        elif isinstance(validator, validators.MinLengthValidator):
            found["min_length"] = validator.limit_value
        elif (
            isinstance(validator, validators.RegexValidator)
            and not validator.inverse_match
            and not validator.flags
            and not isinstance(validator, validators.URLValidator | validators.EmailValidator)
            and type(validator) is validators.RegexValidator
        ):
            # pydantic's Rust regex engine has no \A/\Z; ^/$ mean the same without MULTILINE
            regex: object = validator.regex
            source = regex.pattern if isinstance(regex, re.Pattern) else str(regex)
            found["pattern"] = str(source).replace("\\A", "^").replace("\\Z", "$")
    return {
        key: value
        for key, value in found.items()
        if isinstance(value, str | int | float | Decimal) and not isinstance(value, bool)
    }


def _write_annotation(
    model_field: Field[object, object], imports: _Imports
) -> tuple[str, str, str]:
    attribute, annotation = _read_annotation(model_field, imports, writing=True)
    if model_field.many_to_many:
        return attribute, annotation, " = []"
    if model_field.null:
        return attribute, annotation, " = None"
    if has_db_default(model_field):
        return attribute, f"{annotation} | None", " = None"
    if model_field.has_default() and not callable(model_field.default):
        default: object = model_field.default
        if isinstance(default, Enum):
            default = default.value
        if isinstance(default, str | int | float | bool):
            return attribute, annotation, f" = {_literal(default)}"
    if model_field.blank and isinstance(model_field, _TEXT_FIELDS):
        return attribute, annotation, ' = ""'
    return attribute, annotation, ""


def _as_model_field(model_field: Field[object, object]) -> ModelField:
    return model_field


def _ordering_fields(fields: Sequence[Field[object, object]]) -> list[str]:
    names = [f.name for f in fields if f.primary_key]
    names += [f.name for f in fields if isinstance(f, models.DateField | models.DateTimeField)]
    names += [f.name for f in fields if isinstance(f, models.CharField) and not f.choices][:1]
    return list(dict.fromkeys(names))


def _filter_fields(fields: Sequence[Field[object, object]]) -> dict[str, tuple[str, ...]]:
    filters: dict[str, tuple[str, ...]] = {}
    for model_field in fields:
        if model_field.primary_key or model_field.many_to_many:
            continue
        if (
            isinstance(model_field, models.BooleanField)
            or model_field.choices
            or model_field.many_to_one
        ):
            filters[model_field.name] = ("exact",)
        elif isinstance(model_field, models.DateField | models.DateTimeField):
            filters[model_field.name] = ("gte", "lte")
    return filters


def _literal(value: object) -> str:
    """A Python literal, with strings double-quoted like ruff/black format them."""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        return json.dumps(str(value))
    return repr(value)


def _tuple(values: Sequence[str]) -> str:
    inner = ", ".join(f'"{value}"' for value in values)
    return f"({inner},)" if len(values) == 1 else f"({inner})"


def _missing_lookup(model: type[Model]) -> str:
    python_type = field_type(model._meta.pk)
    if python_type is UUID:
        return "00000000-0000-0000-0000-000000000000"
    return "999999" if python_type is int else "missing"
