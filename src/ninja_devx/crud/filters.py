"""Filter schemas generated from ``search_fields`` and ``filter_fields``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Final

from django.db.models import Model, Q
from ninja import FilterLookup, FilterSchema

from .._internal.cache import owned_cache
from ..exceptions import ControllerConfigError
from ..serialization.pydantic import build_schema
from .fields import field_type, resolve_field

__all__ = ["EmptyFilters", "FilterFields", "filter_schema_for"]

FilterFields = Mapping[str, Sequence[str]]
"""``{"published": ("exact",), "created": ("gte", "lte"), "tags": ("in",)}``."""


class EmptyFilters(FilterSchema):
    pass


def _applied_by_backend(self: FilterSchema, value: object) -> Q:
    return Q()


def _same(value: object) -> object:
    return value


def _text(value: object) -> object:
    return str


def _many(value: object) -> object:
    return list[value]  # type: ignore[valid-type]


def _boolean(value: object) -> object:
    return bool


def _integer(value: object) -> object:
    return int


_LOOKUPS: Final[Mapping[str, Callable[[object], object]]] = MappingProxyType(
    {
        "exact": _same,
        "iexact": _text,
        "contains": _text,
        "icontains": _text,
        "startswith": _text,
        "istartswith": _text,
        "endswith": _text,
        "iendswith": _text,
        "gt": _same,
        "gte": _same,
        "lt": _same,
        "lte": _same,
        "in": _many,
        "isnull": _boolean,
        "year": _integer,
        "month": _integer,
        "day": _integer,
    }
)


def filter_schema_for(controller: type[object]) -> type[FilterSchema]:
    """``filter_schema``, or one generated from ``search_fields``/``filter_fields`` (cached)."""
    _schemas: dict[type[object], type[FilterSchema]] = owned_cache(controller, "filters_schemas")
    if (cached := _schemas.get(controller)) is None:
        cached = _schemas[controller] = _build(controller)
    return cached


def _build(controller: type[object]) -> type[FilterSchema]:
    explicit: type[FilterSchema] | None = getattr(controller, "filter_schema", None)
    search_fields: Sequence[str] = getattr(controller, "search_fields", ())
    filter_fields: FilterFields = getattr(controller, "filter_fields", {})
    if explicit is not None:
        if search_fields or filter_fields:
            raise ControllerConfigError(
                f"{controller.__qualname__}: use either filter_schema or "
                "search_fields/filter_fields, not both"
            )
        return explicit
    if not search_fields and not filter_fields:
        return EmptyFilters

    get_model: Callable[[], type[Model]] = getattr(controller, "get_model")  # noqa: B009 - typed
    model = get_model()
    search_param: str = getattr(controller, "search_param", "search")
    backend_search = search_fields and getattr(controller, "search_backend", None) is not None
    fields: dict[str, tuple[object, None]] = {}
    if search_fields:
        for name in search_fields:
            resolve_field(model, name)  # validates the path
        if backend_search:
            fields[search_param] = (str | None, None)
        else:
            lookups = [f"{name}__icontains" for name in search_fields]
            fields[search_param] = (Annotated[str | None, FilterLookup(lookups)], None)

    for name, lookups_for_field in filter_fields.items():
        python_type = field_type(resolve_field(model, name), choices=True)
        for lookup in lookups_for_field:
            converter = _LOOKUPS.get(lookup)
            if converter is None:
                raise ControllerConfigError(
                    f"{controller.__qualname__}.filter_fields[{name!r}]: unsupported lookup "
                    f"{lookup!r}; expected one of {sorted(_LOOKUPS)}"
                )
            parameter = name if lookup == "exact" else f"{name}__{lookup}"
            expression = name if lookup == "exact" else parameter
            value_type = converter(python_type)
            fields[parameter] = (
                Annotated[value_type | None, FilterLookup(expression)],  # type: ignore[operator]
                None,
            )

    schema = build_schema(f"{controller.__name__}Filters", FilterSchema, fields)
    if backend_search:
        setattr(schema, f"filter_{search_param}", _applied_by_backend)
    return schema
