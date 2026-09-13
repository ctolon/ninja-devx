"""Detect drift between models and the schemas of the controllers exposing them.

``manage.py devx_scaffold --check`` runs this for every mounted ``ModelController``
(or the models given) and fails when a schema no longer matches its model::

    NoteController  NoteOut.priority   error    model IntegerField is int, schema says str
    NoteController  NoteIn             error    required field 'title' is not accepted
    NoteController  NoteOut            info     model field 'archived' is not exposed

Only clear incompatibilities are errors: a value the model can hold that the schema
would reject (type, ``None``, choices), a required field create cannot fill, or a
schema field the model does not have.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal, Union, get_args, get_origin
from uuid import UUID

from django.db.models import CharField, Field, Model
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .._internal.generics import defined_in
from ..crud.controllers import CreateHooks, ModelController
from ..crud.fields import field_type
from ..layers.persistence import model_field

__all__ = ["Drift", "Severity", "schema_drift"]

Severity = Literal["error", "warning", "info"]
_SCALARS: tuple[type[object], ...] = (
    bool,
    int,
    float,
    Decimal,
    str,
    date,
    datetime,
    time,
    timedelta,
    UUID,
)


@dataclass(frozen=True, slots=True)
class Drift:
    controller: str
    """Controller name."""
    location: str
    """Schema or schema field."""
    severity: Severity
    """``error``, ``warning`` or ``info``."""
    message: str
    """What is wrong."""

    def __str__(self) -> str:
        return f"{self.controller}  {self.location}  {self.severity}  {self.message}"


def schema_drift(controller: type[ModelController[Model]]) -> list[Drift]:
    """Mismatches between ``controller``'s model and its output/input schemas."""
    model = controller.get_model()
    found: list[Drift] = []
    name = controller.__qualname__
    output = controller.output_schema()
    if output is not None:
        exposed: set[str] = set()
        for field_name, info in output.model_fields.items():
            attribute = info.alias if isinstance(info.alias, str) else field_name
            location = f"{output.__name__}.{field_name}"
            if hasattr(output, f"resolve_{field_name}") or "." in attribute:
                continue
            db_field = model_field(model, attribute)
            if db_field is None:
                if not hasattr(model, attribute):
                    found.append(
                        Drift(name, location, "error", f"{model.__name__} has no {attribute!r}")
                    )
                continue
            exposed.add(db_field.name)
            found.extend(_compare(name, location, db_field, info, reading=True))
        for db_field in _concrete_fields(model):
            if db_field.name not in exposed and not db_field.primary_key:
                found.append(
                    Drift(
                        name,
                        output.__name__,
                        "info",
                        f"model field {db_field.name!r} is not exposed",
                    )
                )
    schema = controller.input_schema()
    if schema is not None and controller.service_class is None:
        found.extend(_check_input(controller, model, schema))
    return found


def _check_input(
    controller: type[ModelController[Model]], model: type[Model], schema: type[BaseModel]
) -> list[Drift]:
    name = controller.__qualname__
    found: list[Drift] = []
    accepted: set[str] = set()
    for field_name, info in schema.model_fields.items():
        location = f"{schema.__name__}.{field_name}"
        db_field = model_field(model, field_name)
        if db_field is None:
            found.append(
                Drift(name, location, "error", f"{model.__name__} has no field {field_name!r}")
            )
            continue
        accepted.add(db_field.name)
        found.extend(_compare(name, location, db_field, info, reading=False))
    context = {
        field
        for field in (
            controller.owner_field,
            controller.parent and controller.parent.field,
            controller.tenant_field,
        )
        if field
    }
    custom_create = defined_in(controller, "perform_create", through_wrappers=True) not in (
        None,
        CreateHooks,
    )
    for db_field in _concrete_fields(model):
        if custom_create:  # it may fill fields the schema does not accept
            break
        if db_field.name in accepted or db_field.name in context or not _is_required(db_field):
            continue
        found.append(
            Drift(
                name,
                schema.__name__,
                "error",
                f"required field {db_field.name!r} is not accepted; creating fails",
            )
        )
    return found


def _compare(
    controller: str,
    location: str,
    db_field: Field[object, object],
    info: FieldInfo,
    *,
    reading: bool,
) -> list[Drift]:
    found: list[Drift] = []
    annotation = info.annotation
    optional, inner = _split_optional(annotation)
    if db_field.many_to_many:
        return found
    if reading and db_field.null and not optional:
        found.append(
            Drift(controller, location, "error", "the model allows NULL but the schema does not")
        )
    if not reading and optional and not db_field.null:
        found.append(
            Drift(controller, location, "warning", "the schema accepts null, the model does not")
        )
    model_type = field_type(db_field, choices=True)
    if get_origin(model_type) is Literal and get_origin(inner) is Literal:
        model_values, schema_values = set(get_args(model_type)), set(get_args(inner))
        if reading and (missing := model_values - schema_values):
            found.append(
                Drift(
                    controller,
                    location,
                    "error",
                    f"choices {sorted(map(str, missing))} are rejected",
                )
            )
        if not reading and (unknown := schema_values - model_values):
            found.append(
                Drift(
                    controller,
                    location,
                    "error",
                    f"accepts {sorted(map(str, unknown))}, not model choices",
                )
            )
        return found
    if not reading and isinstance(db_field, CharField) and db_field.max_length is not None:
        accepted = _max_length(info)
        if accepted is None or accepted > db_field.max_length:
            found.append(
                Drift(
                    controller,
                    location,
                    "warning",
                    f"accepts strings longer than the column (max_length={db_field.max_length})",
                )
            )
    plain = field_type(db_field)
    if get_origin(inner) is Literal:
        first: object = get_args(inner)[0]
        inner = type(first)
    if not (isinstance(plain, type) and isinstance(inner, type) and inner in _SCALARS):
        return found
    compatible = issubclass(plain, inner) or (inner is float and plain in (int, Decimal))
    if not reading:  # the model coerces on save
        compatible = compatible or issubclass(inner, plain) or plain in (Decimal, float)
    if not compatible:
        found.append(
            Drift(
                controller,
                location,
                "error",
                f"model {db_field.get_internal_type()} is {plain.__name__}, "
                f"schema says {inner.__name__}",
            )
        )
    return found


def _max_length(info: FieldInfo) -> int | None:
    for item in info.metadata:
        limit: object = getattr(item, "max_length", None)
        if isinstance(limit, int):
            return limit
    return None


def _split_optional(annotation: object) -> tuple[bool, object]:
    if get_origin(annotation) in (Union, types.UnionType):
        arguments = [argument for argument in get_args(annotation) if argument is not type(None)]
        optional = len(arguments) < len(get_args(annotation))
        return optional, arguments[0] if len(arguments) == 1 else annotation
    return False, annotation


def _concrete_fields(model: type[Model]) -> list[Field[object, object]]:
    return [
        field
        for field in model._meta.get_fields()
        if isinstance(field, Field) and (field.concrete or field.many_to_many)
    ]


def _is_required(db_field: Field[object, object]) -> bool:
    automatic = getattr(db_field, "auto_now", False) or getattr(db_field, "auto_now_add", False)
    if db_field.primary_key or not db_field.editable or automatic or db_field.many_to_many:
        return False
    return not (db_field.null or db_field.blank or db_field.has_default())
