"""Field visibility by role: one output schema, fields shown only to who may see them.

::

    class EmployeeOut(FieldVisibility, Schema):
        id: int
        name: str
        salary: Annotated[Decimal | None, VisibleTo(IsStaff())] = None
        email: Annotated[str | None, VisibleTo(IsStaff() | IsOwner("user"), hidden="omit")] = None

- ``VisibleTo`` takes the same permissions as operations (``&``, ``|``, ``~`` work).
- A hidden field is serialized as ``null`` (``hidden="null"``, the default) or left out
  (``hidden="omit"``). Declare it optional (``T | None = None``) so OpenAPI and clients know.
- Request-level checks (``IsStaff``, ``HasDjangoPermission``) work on any ``Schema``.
- Object-level checks (``IsOwner``, a ``Policy``) and ``hidden="omit"`` need the
  ``FieldVisibility`` mixin, which knows the object being serialized. Without it an
  object-level rule hides the field: it fails closed.
- Checks run during serialization, with the request Ninja passes to pydantic, so they are
  synchronous; in async operations the user is loaded before (``aprepare_request``).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Never, cast

from django.http import HttpRequest
from pydantic import BaseModel, FieldSerializationInfo, GetCoreSchemaHandler, SerializationInfo
from pydantic.fields import FieldInfo
from pydantic_core import core_schema

from .._permission_eval import denied
from ..security.permissions import BasePermission

if TYPE_CHECKING:
    from pydantic import SerializerFunctionWrapHandler

    from ..security.permissions import AnyPermission

__all__ = [
    "Expandable",
    "FieldVisibility",
    "ResponseShape",
    "VisibleTo",
    "WriteVisibleTo",
    "expandable_fields",
    "forbidden_writes",
    "response_shape",
    "set_response_shape",
    "write_markers",
]

_NO_SOURCE: Final = object()
_source: ContextVar[object] = ContextVar("ninja_devx_visibility_source", default=_NO_SOURCE)
_omitted: ContextVar[set[str] | None] = ContextVar("ninja_devx_visibility_omitted", default=None)
_SOURCE_ATTR: Final = "__ninja_devx_source__"


def _checks_objects(permission: AnyPermission) -> bool:
    if permission.combinator is not None:
        return any(_checks_objects(child) for child in permission.operands)
    check: object = getattr(type(permission), "has_object_permission")  # noqa: B009 - typed as object
    return check is not vars(BasePermission)["has_object_permission"]


def _allows(permission: AnyPermission, request: HttpRequest, source: object) -> bool:
    """Each permission is its request check *and* its object check, then combined.

    (Operation permissions check requests and objects in separate passes, where
    ``IsStaff() | IsOwner()`` would let anyone through the object pass.)
    """
    combinator = permission.combinator
    if combinator == "all":
        return all(_allows(child, request, source) for child in permission.operands)
    if combinator == "any":
        return any(_allows(child, request, source) for child in permission.operands)
    if combinator == "not":
        return not _allows(permission.operands[0], request, source)
    if denied(permission, request, None) is not None:
        return False
    if not _checks_objects(permission):
        return True
    return source is not _NO_SOURCE and denied(permission, request, (source,)) is None


@dataclass(frozen=True, slots=True, init=False)
class VisibleTo:
    permissions: tuple[BasePermission[Never], ...]
    hidden: Literal["null", "omit"]

    def __init__(
        self, *permissions: BasePermission[Never], hidden: Literal["null", "omit"] = "null"
    ) -> None:
        """
        :param permissions: All must allow; each counts as its request check and its object check.
        :param hidden: ``"null"`` serializes a hidden field as ``null``; ``"omit"`` leaves it out
            (needs ``FieldVisibility``).
        """
        if not permissions:
            raise TypeError("VisibleTo needs at least one permission")
        object.__setattr__(self, "permissions", permissions)
        object.__setattr__(self, "hidden", hidden)

    def allows(self, request: HttpRequest | None, source: object) -> bool:
        if request is None:
            return False  # serialized outside a request: fail closed
        return all(_allows(permission, request, source) for permission in self.permissions)

    def __get_pydantic_core_schema__(
        self, source_type: object, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        schema = handler(source_type)
        omit = self.hidden == "omit"

        def serialize(
            model: object,
            value: object,
            next_: SerializerFunctionWrapHandler,
            info: FieldSerializationInfo,
        ) -> object:
            context: object = info.context
            request: object = (
                cast("Mapping[str, object]", context).get("request")
                if isinstance(context, Mapping)
                else None
            )
            allowed = self.allows(
                request if isinstance(request, HttpRequest) else None, _source.get()
            )
            if allowed:
                return next_(value)
            omitted = _omitted.get()
            if omit and omitted is not None:
                omitted.add(info.field_name)
            return None

        serialization = core_schema.wrap_serializer_function_ser_schema(
            serialize, is_field_serializer=True, info_arg=True, schema=schema
        )
        return cast("core_schema.CoreSchema", {**schema, "serialization": serialization})


@dataclass(frozen=True, slots=True)
class WriteVisibleTo:
    """A field only some callers may write; checked by model controllers before persisting.

    Use request-level permissions (``IsStaff``, a policy on ``request``); object-level
    checks fail closed because the object is not known when the payload arrives::

        class ArticleIn(Schema):
            title: str
            featured: Annotated[bool, WriteVisibleTo(IsStaff())] = False

    A non-staff create or update that sends ``featured`` is rejected with 403.
    """

    permissions: tuple[BasePermission[Never], ...]

    def __init__(self, *permissions: BasePermission[Never]) -> None:
        """
        :param permissions: All must allow the request, or the field is rejected.
        """
        if not permissions:
            raise TypeError("WriteVisibleTo needs at least one permission")
        object.__setattr__(self, "permissions", permissions)

    def allows(self, request: HttpRequest | None) -> bool:
        if request is None:
            return False  # no request: fail closed
        return all(_allows(permission, request, _NO_SOURCE) for permission in self.permissions)


def write_markers(schema: type[object]) -> dict[str, WriteVisibleTo]:
    """``{field name: WriteVisibleTo}`` of an input schema."""
    fields: Mapping[str, FieldInfo] = getattr(schema, "model_fields", {})
    return {
        name: item
        for name, info in fields.items()
        for item in info.metadata
        if isinstance(item, WriteVisibleTo)
    }


def forbidden_writes(schema: type[object], sent: object, request: HttpRequest | None) -> set[str]:
    """Names of ``sent`` fields the caller may not write for ``schema``.

    :param schema: The input schema.
    :param sent: Field names actually submitted.
    :param request: The current request.
    """
    markers = write_markers(schema)
    if not markers:
        return set()
    names = {str(name) for name in cast("Mapping[object, object]", sent)}
    return {name for name in names if name in markers and not markers[name].allows(request)}


@dataclass(frozen=True, slots=True)
class Expandable:
    """A relation rendered as its key, or as a nested schema when the client asks.

    ``author: Annotated[int | UserOut, Expandable()]`` is ``1`` by default and
    ``{"id": 1, "username": "ada"}`` with ``?expand=author``. The unexpanded form reads
    only the foreign key column (``author_id``), and the query planner joins the relation
    only when it is expanded. Needs the ``FieldVisibility`` mixin on the schema.
    """

    source: str | None = None
    """Attribute holding the key when not expanded (default ``<field>_id``)."""


@dataclass(frozen=True, slots=True)
class ResponseShape:
    """What the client asked for (``?fields=`` and ``?expand=``) for one response schema."""

    schema: type[object]
    fields: frozenset[str] | None = None
    expand: frozenset[str] = frozenset()


_SHAPE_ATTR: Final = "_ninja_devx_response_shape"


def set_response_shape(request: HttpRequest, shape: ResponseShape) -> None:
    request.__dict__[_SHAPE_ATTR] = shape


def response_shape(request: HttpRequest | None) -> ResponseShape | None:
    if request is None:
        return None
    shape: ResponseShape | None = request.__dict__.get(_SHAPE_ATTR)
    return shape


def expandable_fields(schema: type[object]) -> dict[str, Expandable]:
    """``{field name: Expandable}`` of a pydantic schema."""
    fields: Mapping[str, FieldInfo] = getattr(schema, "model_fields", {})
    return {
        name: item
        for name, info in fields.items()
        for item in info.metadata
        if isinstance(item, Expandable)
    }


class _Overlay:
    """The ORM object, with some attributes replaced (unexpanded relations become keys)."""

    __slots__ = ("_obj", "_values")

    def __init__(self, obj: object, values: Mapping[str, object]) -> None:
        self._obj = obj
        self._values = values

    def __getattr__(self, name: str) -> object:
        if name in self._values:
            return self._values[name]
        found: object = getattr(self._obj, name)
        return found


def _request_of(context: object) -> HttpRequest | None:
    if isinstance(context, Mapping):
        request: object = cast("Mapping[str, object]", context).get("request")
        return request if isinstance(request, HttpRequest) else None
    return None


class FieldVisibility:
    """Schema mixin for response shaping: object-level ``VisibleTo`` rules,
    ``hidden="omit"``, ``?fields=`` sparse fieldsets and ``Expandable`` relations.

    List it before ``Schema``: ``class EmployeeOut(FieldVisibility, Schema)``.
    """

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: object, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        schema = handler(source_type)

        def serialize(
            value: object, next_: SerializerFunctionWrapHandler, info: SerializationInfo
        ) -> object:
            source_token = _source.set(getattr(value, _SOURCE_ATTR, _NO_SOURCE))
            omitted: set[str] = set()
            omitted_token = _omitted.set(omitted)
            try:
                data: object = next_(value)
            finally:
                _source.reset(source_token)
                _omitted.reset(omitted_token)
            shape = response_shape(_request_of(info.context))
            selected = shape.fields if shape is not None and issubclass(cls, shape.schema) else None
            if (omitted or selected is not None) and isinstance(data, dict):
                fields = cast("dict[str, object]", data)
                return {
                    key: item
                    for key, item in fields.items()
                    if key not in omitted and (selected is None or key in selected)
                }
            return data

        def validate(
            value: object,
            next_: core_schema.ValidatorFunctionWrapHandler,
            info: core_schema.ValidationInfo,
        ) -> object:
            source: object = getattr(value, "_obj", value)  # Ninja wraps ORM objects in a getter
            expandable = expandable_fields(cls)
            if expandable and not isinstance(source, Mapping | BaseModel):
                shape = response_shape(_request_of(info.context))
                expand = shape.expand if shape is not None and issubclass(cls, shape.schema) else ()
                keys = {
                    name: getattr(source, marker.source or f"{name}_id", None)
                    for name, marker in expandable.items()
                    if name not in expand
                }
                if keys:
                    value = _Overlay(source, keys)
            instance: object = next_(value)
            object.__setattr__(instance, _SOURCE_ATTR, source)
            return instance

        # Wrapping validation and serialization (not the schema) keeps the fields in OpenAPI.
        serialization = core_schema.wrap_serializer_function_ser_schema(serialize, info_arg=True)
        inner = cast("core_schema.CoreSchema", {k: v for k, v in schema.items() if k != "ref"})
        ref: object = schema.get("ref")
        return core_schema.with_info_wrap_validator_function(
            validate,
            inner,
            ref=ref if isinstance(ref, str) else None,  # keeps the OpenAPI component name
            serialization=serialization,
        )
