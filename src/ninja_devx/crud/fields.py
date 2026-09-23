"""Django model field introspection shared by lookups, filters, nesting and scaffolding."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal, TypeAlias
from uuid import UUID

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Field, ForeignKey, ForeignObjectRel, ManyToManyField, Model

from .._internal.compat import is_composite_primary_key
from .._internal.types import did_you_mean
from ..exceptions import ControllerConfigError

__all__ = ["field_type", "lookup_type", "resolve_field"]

ModelField: TypeAlias = "Field[object, object] | ForeignObjectRel"

_INTEGER: Final = frozenset(
    {
        "AutoField",
        "BigAutoField",
        "SmallAutoField",
        "IntegerField",
        "BigIntegerField",
        "SmallIntegerField",
        "PositiveIntegerField",
        "PositiveBigIntegerField",
        "PositiveSmallIntegerField",
    }
)
_TYPES: Final[Mapping[str, type[object]]] = MappingProxyType(
    {
        "BooleanField": bool,
        "NullBooleanField": bool,
        "DateField": date,
        "DateTimeField": datetime,
        "TimeField": time,
        "DurationField": timedelta,
        "DecimalField": Decimal,
        "FloatField": float,
        "UUIDField": UUID,
        "BinaryField": bytes,
    }
)


def resolve_field(model: type[Model], path: str) -> ModelField:
    """The field at ``path`` (``"pk"``, ``"title"``, ``"author__username"``)."""
    current = model
    field: ModelField | None = None
    for part in path.split("__"):
        try:
            field = current._meta.pk if part == "pk" else current._meta.get_field(part)
        except FieldDoesNotExist as exc:
            names = [f.name for f in current._meta.get_fields()]
            hint = did_you_mean(part, names)
            raise ControllerConfigError(f"{model.__name__} has no field {path!r}.{hint}") from exc
        related: type[Model] | None = getattr(field, "related_model", None)
        if related is not None:
            current = related
    if field is None:  # pragma: no cover - split never returns an empty list
        raise ControllerConfigError(f"Empty field path for {model.__name__}")
    return field


def field_type(field: ModelField, *, choices: bool = False) -> object:
    """The Python type values of ``field`` have; relations use the target's key type.

    With ``choices=True`` a field with choices becomes ``Literal[...]`` of its values.
    """
    if isinstance(field, ManyToManyField):
        return lookup_type(field.related_model, "pk")
    while isinstance(field, ForeignKey):  # includes OneToOneField
        target: Field[object, object] = field.target_field
        field = target
    if isinstance(field, ForeignObjectRel):
        return lookup_type(field.related_model, "pk")
    if choices and field.choices:
        values = tuple(value for value, _ in field.flatchoices)
        return Literal[values]
    internal: str = field.get_internal_type()
    if internal in _INTEGER:
        return int
    return _TYPES.get(internal, str)


def lookup_type(model: type[Model], lookup_field: str) -> type[object]:
    """The Python type of ``model.<lookup_field>`` (used for ``{pk}`` path parameters)."""
    field = resolve_field(model, lookup_field)
    if is_composite_primary_key(field):
        raise ControllerConfigError(
            f"{model.__name__} has a composite primary key, which cannot be one path "
            "segment; set lookup_field to a unique field"
        )
    python_type = field_type(field)
    return python_type if isinstance(python_type, type) else str
