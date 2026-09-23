"""django-filter ``FilterSet`` classes as list filters (``pip install ninja-devx[filters]``).

::

    class PostFilter(django_filters.FilterSet):
        published_after = django_filters.DateFilter("published", lookup_expr="gte")

        class Meta:
            model = Post
            fields = {"title": ["icontains"], "status": ["exact"]}

    class PostController(CRUDController[Post, PostOut, PostIn]):
        filterset_class = PostFilter

Every filter becomes a typed, documented query parameter (range filters become
``<name>_min``/``<name>_max`` or ``<name>_after``/``<name>_before``). The FilterSet itself
still validates and applies the values with the request, so ``method=`` filters, request-
dependent querysets and ``distinct`` behave as they do with DRF. Values the FilterSet
rejects answer 422 like any invalid query parameter.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Protocol, cast
from uuid import UUID

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.db.models import Model, QuerySet
from django.forms.utils import ErrorDict
from django.http import HttpRequest
from django.utils.datastructures import MultiValueDict
from ninja import FilterSchema
from ninja.errors import ValidationError
from pydantic import Field

from .._internal.cache import owned_cache
from ..exceptions import ControllerConfigError

__all__ = ["FilterSetLike", "apply_filterset", "filterset_parameters"]


class FilterSetLike(Protocol):
    """The parts of ``django_filters.FilterSet`` used here."""

    base_filters: Mapping[str, object]

    def __init__(
        self,
        data: MultiValueDict[str, object] | None = None,
        queryset: QuerySet[Model] | None = None,
        *,
        request: HttpRequest | None = None,
    ) -> None: ...

    def is_valid(self) -> bool: ...

    @property
    def errors(self) -> ErrorDict: ...

    @property
    def qs(self) -> QuerySet[Model]: ...


_SCALARS: tuple[tuple[type[forms.Field], type[object]], ...] = (
    (forms.NullBooleanField, bool),
    (forms.BooleanField, bool),
    (forms.IntegerField, int),
    (forms.DecimalField, Decimal),
    (forms.FloatField, float),
    (forms.DateTimeField, datetime),
    (forms.DateField, date),
    (forms.TimeField, time),
    (forms.DurationField, timedelta),
    (forms.UUIDField, UUID),
)


def filterset_parameters(filterset_class: type[FilterSetLike]) -> dict[str, tuple[object, None]]:
    """Query parameters of ``filterset_class`` as ``build_schema`` fields (cached)."""
    cache: dict[str, dict[str, tuple[object, None]]] = owned_cache(filterset_class, "parameters")
    if (parameters := cache.get("parameters")) is None:
        parameters = cache["parameters"] = _build_parameters(filterset_class)
    return parameters


def _build_parameters(filterset_class: type[FilterSetLike]) -> dict[str, tuple[object, None]]:
    parameters: dict[str, tuple[object, None]] = {}
    for filter_name, flt in filterset_class.base_filters.items():
        extra: Mapping[str, object] = getattr(flt, "extra", {})
        description = extra.get("help_text")
        for name, python_type in _parameters(filter_name, flt):
            if not name.isidentifier():
                raise ControllerConfigError(
                    f"{filterset_class.__qualname__}: filter {name!r} is not a valid "
                    "Python identifier, so it cannot be a query parameter"
                )
            parameters[name] = (
                Annotated[
                    python_type | None,  # type: ignore[operator]
                    Field(description=description if isinstance(description, str) else None),
                ],
                None,
            )
    return parameters


def _django_filters(module: str, name: str) -> type[object]:
    """A django-filter class (the package ships no type information)."""
    found: type[object] = getattr(importlib.import_module(f"django_filters.{module}"), name)
    return found


def _parameters(name: str, flt: object) -> Iterator[tuple[str, object]]:
    lookup_choice = _django_filters("fields", "LookupChoiceField")
    csv = _django_filters("fields", "BaseCSVField")
    suffixed = _django_filters("widgets", "SuffixedMultiWidget")
    field_class: type[forms.Field] = getattr(flt, "field_class")  # noqa: B009 - untyped
    extra: Mapping[str, object] = getattr(flt, "extra", {})
    if issubclass(field_class, lookup_choice):
        yield name, str
        yield f"{name}_lookup", str
        return
    if issubclass(field_class, forms.MultiValueField):
        widget: object = extra.get("widget") or field_class.widget
        widget_class = widget if isinstance(widget, type) else type(widget)
        if not issubclass(widget_class, suffixed):
            raise ControllerConfigError(
                f"filter {name!r}: {field_class.__name__} needs a SuffixedMultiWidget to "
                "become query parameters"
            )
        suffixes: list[str | None] = getattr(widget, "suffixes", [])
        for suffix in suffixes:
            yield (f"{name}_{suffix}" if suffix else name), _range_part(field_class)
        return
    if issubclass(field_class, csv):
        yield name, str  # "a,b": split and validated by the filter's own field
        return
    if issubclass(field_class, forms.MultipleChoiceField | forms.ModelMultipleChoiceField):
        yield name, list[str]
        return
    yield name, next((python for form, python in _SCALARS if issubclass(field_class, form)), str)


def _range_part(field_class: type[forms.Field]) -> type[object]:
    """The type of each end of a django-filter range field."""
    for name, python in (
        ("DateTimeRangeField", datetime),
        ("IsoDateTimeRangeField", datetime),
        ("DateRangeField", date),
        ("TimeRangeField", time),
    ):
        if issubclass(field_class, _django_filters("fields", name)):
            return python
    return Decimal if field_class is _django_filters("fields", "RangeField") else str


def apply_filterset(
    filterset_class: type[FilterSetLike],
    request: HttpRequest,
    queryset: QuerySet[Model],
    filters: FilterSchema,
) -> QuerySet[Model]:
    """``queryset`` filtered by ``filterset_class`` with the validated query parameters.

    :raises ninja.errors.ValidationError: The FilterSet's form rejected a value (422).
    """
    data: MultiValueDict[str, object] = MultiValueDict()
    for name in filterset_parameters(filterset_class):
        value: object = getattr(filters, name, None)
        if isinstance(value, list):
            data.setlist(name, cast("list[object]", value))
        elif value is not None:
            data.setlist(name, [value])
    filterset = filterset_class(data=data, queryset=queryset, request=request)
    if not filterset.is_valid():
        raise ValidationError(
            [
                {
                    "type": error.code or "invalid",
                    "loc": ["query"] if name == NON_FIELD_ERRORS else ["query", name],
                    "msg": message,
                }
                for name, errors in filterset.errors.as_data().items()
                for error in errors
                for message in error.messages
            ]
        )
    return filterset.qs
