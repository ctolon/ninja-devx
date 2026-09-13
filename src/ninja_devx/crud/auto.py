"""CRUD controllers whose schemas are generated from the model by Ninja's ``create_schema``.

::

    class PostController(AutoCRUDController[Post]):
        read_only_fields = ("author", "created")
        write_only_fields = ("secret",)

- The output schema (``PostOut``) has ``schema_fields`` minus ``schema_exclude`` and
  ``write_only_fields``.
- The input schema (``PostIn``) also leaves out the primary key, non-editable fields
  (``auto_now``) and ``read_only_fields``.
- Everything else (filters, permissions, tenancy, pagination...) is configured as on
  ``CRUDController``. Switch to explicit schemas once they need custom fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar, Generic, Literal, TypeVar

from django.db.models import Field, Model
from ninja import Schema
from ninja.orm import create_schema
from pydantic import BaseModel

from .._internal.cache import owned_cache
from .._internal.types import did_you_mean
from ..exceptions import ControllerConfigError
from .controllers import CRUDController, InT, ModelT, OutT, ReadOnlyModelController

__all__ = [
    "AsyncAutoCRUDController",
    "AsyncAutoReadOnlyController",
    "AutoCRUDController",
    "AutoReadOnlyController",
    "AutoSchemas",
    "model_schemas",
]


class AutoSchemas:
    """Which model fields the generated schemas contain."""

    schema_fields: ClassVar[Sequence[str] | Literal["__all__"]] = "__all__"
    """Model fields in the schemas (field names; ``"__all__"``: every concrete and m2m field)."""
    schema_exclude: ClassVar[Sequence[str]] = ()
    """Fields left out of both schemas."""
    read_only_fields: ClassVar[Sequence[str]] = ()
    """Fields in responses only. The primary key and non-editable fields always are."""
    write_only_fields: ClassVar[Sequence[str]] = ()
    """Fields in requests only (``password``)."""

    @classmethod
    def derive_type_arguments(cls, arguments: Mapping[TypeVar, object]) -> Mapping[TypeVar, object]:
        model = arguments.get(ModelT)
        if not (isinstance(model, type) and issubclass(model, Model)):
            return {}  # still generic: ``AutoCRUDController`` itself, or an abstract base
        output, input_ = model_schemas(
            model,
            fields=cls.schema_fields,
            exclude=cls.schema_exclude,
            read_only=cls.read_only_fields,
            write_only=cls.write_only_fields,
        )
        return {OutT: output, InT: input_}


def model_schemas(
    model: type[Model],
    *,
    fields: Sequence[str] | Literal["__all__"] = "__all__",
    exclude: Sequence[str] = (),
    read_only: Sequence[str] = (),
    write_only: Sequence[str] = (),
) -> tuple[type[Schema], type[Schema]]:
    """``(<Model>Out, <Model>In)`` generated with Ninja's ``create_schema`` (cached).

    :param model: The Django model.
    :param fields: Field names to include, or ``"__all__"``.
    :param exclude: Field names left out of both schemas.
    :param read_only: Field names only in the output schema.
    :param write_only: Field names only in the input schema.
    """
    _schemas: dict[tuple[object, ...], tuple[type[Schema], type[Schema]]] = owned_cache(
        model, "model_schemas"
    )
    key = (
        model,
        fields if isinstance(fields, str) else tuple(fields),
        *map(tuple, (exclude, read_only, write_only)),
    )
    if (cached := _schemas.get(key)) is not None:
        return cached
    available = _field_names(model)
    for group in (() if isinstance(fields, str) else fields, exclude, read_only, write_only):
        for name in group:
            if name not in available:
                raise ControllerConfigError(
                    f"{model.__name__} has no field {name!r}.{did_you_mean(name, available)}"
                )
    chosen = [
        name for name in (available if isinstance(fields, str) else fields) if name not in exclude
    ]
    options = model._meta
    automatic = {
        field.name for field in options.concrete_fields if field.primary_key or not field.editable
    }
    output_fields = [name for name in chosen if name not in write_only]
    input_fields = [name for name in chosen if name not in automatic and name not in read_only]
    output = create_schema(model, name=f"{model.__name__}Out", fields=output_fields)
    input_ = create_schema(model, name=f"{model.__name__}In", fields=input_fields)
    _schemas[key] = (output, input_)
    return output, input_


def _field_names(model: type[Model]) -> list[str]:
    options = model._meta
    fields: list[Field[object, object]] = [*options.concrete_fields, *options.many_to_many]
    return [field.name for field in fields]


class AutoReadOnlyController(
    ReadOnlyModelController[ModelT, BaseModel], AutoSchemas, Generic[ModelT]
):
    """``GET /`` and ``GET /{pk}`` with a generated output schema."""


class AutoCRUDController(
    CRUDController[ModelT, BaseModel, BaseModel], AutoSchemas, Generic[ModelT]
):
    """Full CRUD with generated ``<Model>Out`` / ``<Model>In`` schemas."""


class AsyncAutoReadOnlyController(AutoReadOnlyController[ModelT], Generic[ModelT]):
    mode: ClassVar[Literal["sync", "async", "auto"]] = "async"


class AsyncAutoCRUDController(AutoCRUDController[ModelT], Generic[ModelT]):
    mode: ClassVar[Literal["sync", "async", "auto"]] = "async"
