"""Annotations resolved per controller when its router is built."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping, Sequence
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Final,
    Literal,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
)
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Model
from django.http import Http404, HttpRequest
from ninja import FilterSchema, Schema
from ninja.params.functions import Path, Query
from pydantic import Field as SchemaField
from pydantic import create_model

from .._internal.cache import owned_cache
from .._internal.generics import LazyAnnotation
from .._internal.i18n import not_found
from ..exceptions import ControllerConfigError
from ..routing.bindings import Arguments, BindingMarker, ParameterBinding
from ..security.permissions import acheck_object_permissions, check_object_permissions
from .fields import lookup_type
from .filters import filter_schema_for

if TYPE_CHECKING:
    from ..dependencies.instances import Invocation
    from ..routing.controller import Controller
    from ..routing.operations import OperationSpec

__all__ = ["Filters", "Instance", "Locked", "Lookup", "Ordering", "OrderingSchema"]

SchemaT = TypeVar("SchemaT")
ModelT = TypeVar("ModelT", bound=Model)

_PATH_CONVERTERS: Final[Mapping[type[object], str]] = MappingProxyType({int: "int", UUID: "uuid"})


# --- Lookups -----------------------------------------------------------------------


def controller_model(controller: type[object]) -> type[Model]:
    get_model = getattr(controller, "get_model", None)
    if get_model is None:
        raise ControllerConfigError(f"{controller.__qualname__} is not a ModelController")
    model: type[Model] = get_model()
    return model


def controller_lookup_type(controller: type[object]) -> type[object]:
    """The Python type of the controller's ``lookup_field``."""
    lookup_field: str = getattr(controller, "lookup_field", "pk")
    return lookup_type(controller_model(controller), lookup_field)


def lookup_param(controller: type[object]) -> str:
    """The URL name of the lookup segment (``lookup_param``, ``"pk"`` by default)."""
    name: str = getattr(controller, "lookup_param", "pk")
    return name


def has_lookup(path: str, controller: type[object]) -> bool:
    name = lookup_param(controller)
    return f"{{{name}}}" in path or f":{name}}}" in path or "{pk}" in path


def with_path_converter(path: str, controller: type[object]) -> str:
    """``/{pk}`` -> ``/{<lookup_param>}``, or ``/{int:<lookup_param>}`` with ``lookup_converter``.

    Without a converter an invalid id reaches Ninja and gets a JSON 422; with one, Django
    answers 404 itself, which keeps routes of routers mounted later (``/posts/me``) reachable.
    """
    use_converter: bool = getattr(controller, "lookup_converter", False)
    converter = _PATH_CONVERTERS.get(controller_lookup_type(controller)) if use_converter else None
    name = lookup_param(controller)
    segment = f"{{{converter}:{name}}}" if converter else f"{{{name}}}"
    return path.replace("{pk}", segment).replace(f"{{{name}}}", segment)


def _lookup_annotation(key_type: object, controller: type[object]) -> object:
    name = lookup_param(controller)
    return Annotated[key_type, Path(alias=name)] if name != "pk" else Annotated[key_type, Path()]


class _LookupMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        key_type = controller_lookup_type(controller)
        return (
            _lookup_annotation(key_type, controller)
            if lookup_param(controller) != "pk"
            else key_type
        )


Lookup: TypeAlias = Annotated[int | str | UUID, _LookupMarker()]
"""Path parameter typed like the controller's ``lookup_field`` (the primary key by default)."""


# --- Instance ----------------------------------------------------------------------


class _ObjectLookup(Protocol):
    def get_object(self, request: HttpRequest, lookup: object, *, lock: bool = False) -> Model: ...
    def aget_object(self, request: HttpRequest, lookup: object) -> Awaitable[Model]: ...


class _InstanceMarker(BindingMarker):
    def __init__(self, *, lock: bool = False) -> None:
        self.lock = lock

    def bind(
        self,
        parameter: inspect.Parameter,
        annotation: object,
        controller: type[Controller],
        spec: OperationSpec,
    ) -> ParameterBinding:
        if not (isinstance(annotation, type) and issubclass(annotation, Model)):
            raise ControllerConfigError(
                f"{controller.__qualname__}: Instance[...] needs a model, got {annotation!r}"
            )
        model = annotation
        if not has_lookup(spec.path, controller):
            raise ControllerConfigError(
                f"{controller.__qualname__}: {parameter.name}: Instance[{model.__name__}] "
                f"needs a {{pk}} segment in {spec.path!r}"
            )
        own_model = getattr(controller, "get_model", None) is not None and (
            controller_model(controller) is model
        )
        key_type = controller_lookup_type(controller) if own_model else lookup_type(model, "pk")
        exposed = inspect.Parameter(
            "pk",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=_lookup_annotation(key_type, controller),
        )

        def resolve(invocation: Invocation, arguments: Arguments) -> object:
            lookup = arguments.pop("pk")
            if own_model:
                controller = cast(_ObjectLookup, invocation.controller)
                return controller.get_object(invocation.request, lookup, lock=self.lock)
            return _fetch(model, invocation.request, lookup, lock=self.lock)

        async def aresolve(invocation: Invocation, arguments: Arguments) -> object:
            lookup = arguments.pop("pk")
            if own_model:
                controller = cast(_ObjectLookup, invocation.controller)
                return await controller.aget_object(invocation.request, lookup)
            return await _afetch(model, invocation.request, lookup)

        return ParameterBinding(
            name=parameter.name,
            parameters=(exposed,),
            resolve=resolve,
            aresolve=aresolve,
            documented_errors=frozenset({404}),
        )


def _fetch(
    model: type[Model], request: HttpRequest, lookup: object, *, lock: bool = False
) -> Model:
    queryset = model._default_manager.all()
    try:
        instance = (queryset.select_for_update() if lock else queryset).get(pk=lookup)
    except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
        raise Http404(not_found(model)) from exc
    check_object_permissions(request, instance)
    return instance


async def _afetch(model: type[Model], request: HttpRequest, lookup: object) -> Model:
    try:
        instance = await model._default_manager.aget(pk=lookup)
    except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
        raise Http404(not_found(model)) from exc
    await acheck_object_permissions(request, instance)
    return instance


Instance: TypeAlias = Annotated[ModelT, _InstanceMarker()]
"""A model instance loaded from the ``{pk}`` path segment, with 404 and object permissions."""

Locked: TypeAlias = Annotated[ModelT, _InstanceMarker(lock=True)]
"""Like ``Instance`` but loaded with ``select_for_update()`` (use on ``atomic=True`` operations)."""


# --- Filters and ordering ----------------------------------------------------------


def query_schema(schema: type[Schema]) -> object:
    """A query-parameter schema; schemas without required fields may be omitted entirely."""
    if any(field.is_required() for field in schema.model_fields.values()):
        return Annotated[schema, Query()]
    return Annotated[schema, Query(schema())]


class _FiltersMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return query_schema(filter_schema_for(controller))


Filters: TypeAlias = Annotated[FilterSchema, _FiltersMarker()]
"""Query parameters from ``filter_schema`` or ``search_fields``/``filter_fields``."""


class OrderingSchema(Schema):
    """Ordering chosen by the client; the base class exposes no parameters."""

    def values(self) -> Sequence[str]:
        return ()


class _SelectableOrdering(OrderingSchema):
    ordering: list[str] = SchemaField(default_factory=list)

    def values(self) -> Sequence[str]:
        return self.ordering  # the query parameter is named by ``ordering_param``


class _OrderingMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return query_schema(ordering_schema(controller))


Ordering: TypeAlias = Annotated[OrderingSchema, _OrderingMarker()]
"""Query parameter ``ordering`` restricted to the controller's ``ordering_fields``."""


def ordering_schema(controller: type[object]) -> type[OrderingSchema]:
    """The ordering schema of a controller, cached so OpenAPI component names stay stable."""
    _ordering_schemas: dict[type[object], type[OrderingSchema]] = owned_cache(
        controller, "annotations_ordering_schemas"
    )
    if (cached := _ordering_schemas.get(controller)) is None:
        cached = _ordering_schemas[controller] = _build_ordering_schema(controller)
    return cached


def _build_ordering_schema(controller: type[object]) -> type[OrderingSchema]:
    fields: Sequence[str] = getattr(controller, "ordering_fields", ())
    default: Sequence[str] = getattr(controller, "default_ordering", ())
    choices = tuple(value for name in fields for value in (name, f"-{name}"))
    if fields and (invalid := [value for value in default if value not in choices]):
        raise ControllerConfigError(
            f"{controller.__qualname__}.default_ordering {invalid} not in ordering_fields"
        )
    if not fields:
        return OrderingSchema
    choice_type: object = Literal[choices]
    schema: type[OrderingSchema] = create_model(
        f"{controller.__name__}Ordering",
        __base__=_SelectableOrdering,
        ordering=(
            list[choice_type],  # type: ignore[valid-type]
            SchemaField(
                default=[*default], alias=getattr(controller, "ordering_param", "ordering")
            ),
        ),
    )
    return schema
