"""Write a parent and its child collections together, in one request and transaction.

::

    class OrderController(
        NestedWritesMixin[Order, OrderIn], CRUDController[Order, OrderOut, OrderIn]
    ):
        nested = {"items": Nested(OrderItem, "order", OrderItemIn)}

``OrderIn`` must declare ``items: list[OrderItemIn]``; ``OrderItem.order`` is the foreign
key back to ``Order``. On create, the parent is saved and then every item, all inside the
operation's ``write_scope``. On update (``PUT``/``PATCH``), an item sent with its ``key``
field (default ``"id"``) is matched against the parent's existing children and updated in
place; an item sent without it is created; existing children whose key does not appear in
the payload are deleted unless ``Nested(..., remove_missing=False)``. A ``PATCH`` that
does not send the field at all leaves the children untouched. Validation errors on an
item are reported at ``<field>.<index>.<...>``, e.g. ``items.2.quantity``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Generic, cast

from django.db.models import Model
from django.http import HttpRequest
from ninja.errors import ValidationError
from pydantic import BaseModel

from .._internal.cache import owned_cache
from ..configuration.settings import class_setting, get_settings
from ..exceptions import ControllerConfigError
from ..layers.errors import ValidationFailed
from ..routing.operations import OperationSpec
from .controllers import InT, ModelController, ModelT
from .fields import resolve_field
from .persistence import save_instance, unknown_fields

__all__ = ["Nested", "NestedWritesMixin"]


@dataclass(frozen=True, slots=True)
class Nested:
    """One child collection written alongside its parent.

    ``Nested(OrderItem, "order", OrderItemIn)``: ``OrderItem.order`` is the foreign key to
    the parent, ``OrderItemIn`` validates each item.
    """

    model: type[Model]
    """The child model."""
    field: str
    """The child's foreign key to the parent."""
    schema: type[BaseModel]
    """Input schema validating each item."""
    key: str = "id"
    """Field matching a payload item against an existing child on update."""
    remove_missing: bool = True
    """On update, delete existing children whose key is absent from the payload."""


class NestedWritesMixin(ModelController[ModelT], Generic[ModelT, InT]):
    """Adds child collections to create and update, declared with ``nested``.

    List it before the CRUD base so its ``perform_create``/``perform_update`` win, like
    ``SoftDeleteMixin``.
    """

    nested: ClassVar[Mapping[str, Nested]] = MappingProxyType({})
    """Child collections keyed by their field on the input schema."""

    @classmethod
    def nested_config(cls) -> Mapping[str, Nested]:
        """``nested``, validated once against the model and input schema."""
        cache: dict[type[object], Mapping[str, Nested]] = owned_cache(cls, "nested_resolved")
        if (cached := cache.get(cls)) is None:
            model = cls.get_model()
            schema = cls.input_schema()
            if cls.nested and schema is None:
                raise ControllerConfigError(f"{cls.__qualname__}.nested needs an input schema")
            for field_name, nested in cls.nested.items():
                related = getattr(resolve_field(nested.model, nested.field), "related_model", None)
                if related is not model:
                    raise ControllerConfigError(
                        f"{cls.__qualname__}: nested {field_name!r}: "
                        f"{nested.model.__name__}.{nested.field} does not point to "
                        f"{model.__name__}"
                    )
                if schema is not None and field_name not in schema.model_fields:
                    raise ControllerConfigError(
                        f"{cls.__qualname__}: {schema.__name__} must declare "
                        f"`{field_name}: list[{nested.schema.__name__}]`"
                    )
                if nested.field in nested.schema.model_fields:
                    raise ControllerConfigError(
                        f"{cls.__qualname__}: nested {field_name!r} schema must not declare "
                        f"{nested.field!r}; it is set from the parent"
                    )
                if unknown := unknown_fields(nested.model, nested.schema.model_fields):
                    raise ControllerConfigError(
                        f"{cls.__qualname__}: nested {field_name!r} schema fields {unknown} do "
                        f"not exist on {nested.model.__name__}"
                    )
            cached = cache[cls] = dict(cls.nested)
        return cached

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        cls.nested_config()
        return super().customize_operation(name, spec)

    def perform_create(self, request: HttpRequest, payload: InT) -> ModelT:
        """Create the parent, then every declared child collection, one transaction."""
        nested = type(self).nested_config()
        dumped = payload.model_dump()
        children = {field_name: dumped.pop(field_name) for field_name in nested}
        data = {**dumped, **self.context_data(request)}
        instance = self.get_service(request).create(data)
        database = self.write_database(request)
        errors: list[dict[str, object]] = []
        for field_name, items in children.items():
            errors.extend(
                self._create_children(instance, nested[field_name], field_name, items, database)
            )
        if errors:
            raise ValidationError(errors)
        return instance

    def perform_update(
        self, request: HttpRequest, instance: ModelT, data: Mapping[str, object]
    ) -> ModelT:
        """Update the parent's own fields, then sync any child collections present in ``data``."""
        nested = type(self).nested_config()
        present: dict[str, Sequence[Mapping[str, object]]] = {
            field_name: cast("Sequence[Mapping[str, object]]", data[field_name])
            for field_name in nested
            if field_name in data
        }
        parent_data = {key: value for key, value in data.items() if key not in nested}
        updated = super().perform_update(request, instance, parent_data)
        if present:
            database = self.write_database(request)
            errors: list[dict[str, object]] = []
            for field_name, items in present.items():
                errors.extend(
                    self._sync_children(updated, nested[field_name], field_name, items, database)
                )
            if errors:
                raise ValidationError(errors)
        return updated

    def _create_children(
        self,
        parent: ModelT,
        nested: Nested,
        field_name: str,
        items: Sequence[Mapping[str, object]],
        database: str,
    ) -> list[dict[str, object]]:
        validate = class_setting(type(self), "validate_model", get_settings().validate_model)
        errors: list[dict[str, object]] = []
        for index, raw in enumerate(items):
            data = {**raw, nested.field: parent}
            try:
                save_instance(nested.model(), data, validate=validate, using=database)
            except ValidationFailed as exc:
                errors.extend(_child_errors(field_name, index, exc))
        return errors

    def _sync_children(
        self,
        parent: ModelT,
        nested: Nested,
        field_name: str,
        items: Sequence[Mapping[str, object]],
        database: str,
    ) -> list[dict[str, object]]:
        validate = class_setting(type(self), "validate_model", get_settings().validate_model)
        existing = {
            getattr(child, nested.key): child
            for child in nested.model._default_manager.using(database).filter(
                **{nested.field: parent}
            )
        }
        matched: set[object] = set()
        errors: list[dict[str, object]] = []
        for index, raw in enumerate(items):
            child_key = raw.get(nested.key)
            target = existing.get(child_key) if child_key is not None else None
            data = {**raw, nested.field: parent}
            try:
                if target is not None:
                    matched.add(child_key)
                    save_instance(target, data, validate=validate, using=database)
                else:
                    save_instance(nested.model(), data, validate=validate, using=database)
            except ValidationFailed as exc:
                errors.extend(_child_errors(field_name, index, exc))
        if nested.remove_missing:
            for child_key, child in existing.items():
                if child_key not in matched:
                    child.delete(using=database)
        return errors


def _child_errors(field_name: str, index: int, exc: ValidationFailed) -> list[dict[str, object]]:
    return [
        {
            "type": exc.code,
            "loc": ["body", field_name, index, *([field] if field else [])],
            "msg": message,
        }
        for field, messages in exc.errors.items()
        for message in messages
    ]
