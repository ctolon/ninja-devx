"""``GET /meta`` describing a model controller's fields, for building forms and admin UIs.

::

    class NoteController(MetaMixin[Note], CRUDController[Note, NoteOut, NoteIn]):
        filter_fields = {"status": ("exact",)}
        ordering_fields = ("priority",)
        search_fields = ("text",)

The response lists every input and output schema field with its type name, whether it is
required or read-only, its max length and its choices (from the matching model field's
``choices``, or from an ``enum.Enum`` schema type), plus the controller's ``filter_fields``,
``ordering_fields`` and ``search_fields`` names. A field is read-only when the output schema
carries it but the input schema does not. The structure needs no database access, so it is
computed once and cached per controller class.
"""

from __future__ import annotations

import enum
import types
from collections.abc import Mapping, Sequence
from typing import Generic, Literal, Union, cast, get_args, get_origin

from annotated_types import MaxLen
from django.db.models import Model
from django.http import HttpRequest
from ninja import Schema
from pydantic.fields import FieldInfo

from .._internal.cache import owned_cache
from ..routing.operations import async_variant, get
from .controllers import ModelController, ModelT
from .persistence import model_field

__all__ = ["ChoiceOut", "ControllerMeta", "FieldMeta", "MetaMixin"]


class ChoiceOut(Schema):
    """One ``value``/``label`` choice."""

    value: str | int | bool
    label: str


class FieldMeta(Schema):
    """What a form or admin UI needs to know about one schema field."""

    type: str
    required: bool
    read_only: bool
    max_length: int | None = None
    choices: list[ChoiceOut] | None = None


class ControllerMeta(Schema):
    """The body of ``GET /meta``."""

    input: dict[str, FieldMeta]
    output: dict[str, FieldMeta]
    filter_fields: list[str]
    ordering_fields: list[str]
    search_fields: list[str]


class MetaMixin(ModelController[ModelT], Generic[ModelT]):
    """Adds ``GET /meta``, describing the input and output schemas."""

    @get("/meta", response=ControllerMeta)
    def meta(self, request: HttpRequest) -> ControllerMeta:
        return type(self).controller_meta()

    @async_variant(meta)
    async def ameta(self, request: HttpRequest) -> ControllerMeta:
        return type(self).controller_meta()

    @classmethod
    def controller_meta(cls) -> ControllerMeta:
        cache: dict[type[object], ControllerMeta] = owned_cache(cls, "meta")
        if (cached := cache.get(cls)) is None:
            cached = cache[cls] = _build_meta(cast("type[MetaMixin[Model]]", cls))
        return cached


def _build_meta(cls: type[MetaMixin[Model]]) -> ControllerMeta:
    model = cls.get_model()
    input_schema = cls.input_schema()
    output_schema = cls.output_schema()
    input_fields: Mapping[str, FieldInfo] = input_schema.model_fields if input_schema else {}
    output_fields: Mapping[str, FieldInfo] = output_schema.model_fields if output_schema else {}
    return ControllerMeta(
        input={
            name: _field_meta(model, name, info, writable=True)
            for name, info in input_fields.items()
        },
        output={
            name: _field_meta(model, name, info, writable=name in input_fields)
            for name, info in output_fields.items()
        },
        filter_fields=sorted(cast("Mapping[str, object]", getattr(cls, "filter_fields", {}))),
        ordering_fields=list(cast("Sequence[str]", getattr(cls, "ordering_fields", ()))),
        search_fields=list(cast("Sequence[str]", getattr(cls, "search_fields", ()))),
    )


def _field_meta(model: type[Model], name: str, info: FieldInfo, *, writable: bool) -> FieldMeta:
    enum_type = _enum_type(info.annotation)
    choices: list[ChoiceOut] | None = _enum_choices(enum_type) if enum_type is not None else None
    max_length = _annotated_max_length(info)
    field = model_field(model, info.alias if isinstance(info.alias, str) else name)
    if field is not None:
        if choices is None and field.choices:
            choices = [
                ChoiceOut(value=cast("str | int | bool", value), label=str(label))
                for value, label in field.flatchoices
            ]
        if max_length is None:
            max_length = getattr(field, "max_length", None)
    return FieldMeta(
        type=_type_name(info.annotation),
        required=info.is_required(),
        read_only=not writable,
        max_length=max_length,
        choices=choices,
    )


def _strip_optional(annotation: object) -> object:
    if get_origin(annotation) in (Union, types.UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _enum_type(annotation: object | None) -> type[enum.Enum] | None:
    unwrapped = _strip_optional(annotation)
    if isinstance(unwrapped, type) and issubclass(unwrapped, enum.Enum):
        return unwrapped
    return None


def _enum_choices(enum_type: type[enum.Enum]) -> list[ChoiceOut]:
    return [
        ChoiceOut(
            value=cast("str | int | bool", member.value),
            label=str(getattr(member, "label", member.name)),
        )
        for member in enum_type
    ]


def _annotated_max_length(info: FieldInfo) -> int | None:
    for item in info.metadata:
        if isinstance(item, MaxLen):
            return item.max_length
    return None


def _type_name(annotation: object | None) -> str:
    unwrapped = _strip_optional(annotation)
    origin = get_origin(unwrapped)
    if origin in (list, set, tuple, frozenset):
        args = get_args(unwrapped)
        return f"list[{_type_name(args[0])}]" if args else "list"
    if origin is Literal:
        values = get_args(unwrapped)
        return _type_name(cast("type[object]", type(values[0]))) if values else "str"
    if isinstance(unwrapped, type):
        return unwrapped.__name__
    return "object"
