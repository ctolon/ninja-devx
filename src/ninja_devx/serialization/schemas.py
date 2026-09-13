"""Schema helpers: typed partial updates and read-only / write-only fields.

``Patch[ArticleIn]`` validates the fields of ``ArticleIn`` that were sent (all optional)
and gives the handler a ``PatchData``: a dict with ``.changed`` and ``.apply(obj)``.

``ReadOnly`` / ``WriteOnly`` mark fields on one schema; ``Input[S]`` hides read-only
fields from requests, ``Output[S]`` hides write-only fields from responses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Final, Generic, TypeVar, cast

from ninja import Schema
from ninja.params.functions import Body
from ninja.patch_dict import ModelToDict
from pydantic import BaseModel, BeforeValidator
from pydantic import Field as SchemaField
from pydantic.fields import FieldInfo
from pydantic.json_schema import SkipJsonSchema

from .._internal.cache import owned_cache
from .._internal.generics import LazyAnnotation, find_unbound
from .pydantic import build_schema

__all__ = ["Input", "Output", "Patch", "PatchData", "ReadOnly", "WriteOnly"]

SchemaT = TypeVar("SchemaT", bound=BaseModel)
T = TypeVar("T")


class PatchData(dict[str, object], Generic[SchemaT]):
    """The validated fields a client sent for a partial update of ``SchemaT``."""

    @property
    def changed(self) -> frozenset[str]:
        return frozenset(self)

    def apply(self, target: T) -> T:
        """Set every sent field on ``target`` (no save)."""
        for name, value in self.items():
            setattr(target, name, value)
        return target


def patch_type(schema: type[BaseModel]) -> type[PatchData[BaseModel]]:
    """The body type for ``Patch[schema]`` (cached; OpenAPI name ``<Schema>Patch``)."""
    _patch_types: dict[type[BaseModel], type[PatchData[BaseModel]]] = owned_cache(
        schema, "schemas_patch_types"
    )
    cached = _patch_types.get(schema)
    if cached is not None:
        return cached
    # Built from pydantic's resolved fields, so postponed annotations work.
    fields: dict[str, tuple[object, object]] = {
        name: (
            info.rebuild_annotation() | None,
            SchemaField(None, alias=info.alias, description=info.description),
        )
        for name, info in schema.model_fields.items()
    }
    optional = build_schema(f"{schema.__name__}Patch", Schema, fields)

    class _Patch(PatchData[BaseModel], ModelToDict):
        _wrapped_model = optional
        _wrapped_model_dump_params = {"exclude_unset": True}  # noqa: RUF012

        @classmethod
        def _validate(cls, input_value: BaseModel) -> PatchData[BaseModel]:
            return PatchData(input_value.model_dump(exclude_unset=True))

    _Patch.__name__ = _Patch.__qualname__ = f"{schema.__name__}Patch"
    _patch_types[schema] = _Patch
    return _Patch


class _PatchMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return Annotated[patch_type(cast("type[BaseModel]", annotation)), Body()]


if TYPE_CHECKING:
    Patch = PatchData
else:

    class _PatchAlias:
        def __getitem__(self, schema: object) -> object:
            if find_unbound(schema):
                return Annotated[schema, _PatchMarker()]
            return Annotated[patch_type(schema), Body()]

    Patch = _PatchAlias()


# --- Read-only / write-only fields -------------------------------------------------


class _Marker:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


READ_ONLY: Final = _Marker("ReadOnly")
WRITE_ONLY: Final = _Marker("WriteOnly")

ReadOnly = Annotated[T, READ_ONLY]
"""A field clients cannot set: hidden and ignored in ``Input[S]``."""
WriteOnly = Annotated[T, WRITE_ONLY]
"""A field never returned: hidden and not serialized in ``Output[S]``."""


def _discard(value: object) -> None:
    return None


def _has_marker(info: FieldInfo, marker: _Marker) -> bool:
    return marker in info.metadata


def derive(schema: type[BaseModel], hidden: _Marker, suffix: str) -> type[BaseModel]:
    _derived: dict[tuple[type[BaseModel], _Marker], type[BaseModel]] = owned_cache(
        schema, "derived_schemas"
    )
    key = (schema, hidden)
    if (cached := _derived.get(key)) is not None:
        return cached
    fields: dict[str, tuple[object, object]] = {}
    for name, info in schema.model_fields.items():
        if _has_marker(info, hidden):
            # Hidden from the JSON schema, never serialized, and whatever is sent is dropped.
            annotation: object = Annotated[
                SkipJsonSchema[info.rebuild_annotation() | None],  # type: ignore[misc]
                BeforeValidator(_discard),
            ]
            fields[name] = (annotation, SchemaField(None, exclude=True))
        else:
            fields[name] = (info.rebuild_annotation(), info)
    derived = build_schema(f"{schema.__name__}{suffix}", Schema, fields)
    _derived[key] = derived
    return derived


if TYPE_CHECKING:
    Input = Annotated[SchemaT, "Input"]
    Output = Annotated[SchemaT, "Output"]
else:

    class _DerivedAlias:
        def __init__(self, hidden: _Marker, suffix: str) -> None:
            self.hidden = hidden
            self.suffix = suffix

        def __getitem__(self, schema: type[BaseModel]) -> type[BaseModel]:
            return derive(schema, self.hidden, self.suffix)

    Input = _DerivedAlias(READ_ONLY, "Input")
    Output = _DerivedAlias(WRITE_ONLY, "Output")


def as_mapping(data: Mapping[str, object] | BaseModel) -> Mapping[str, object]:
    return data.model_dump() if isinstance(data, BaseModel) else data
