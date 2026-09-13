"""Bulk create, update and delete with a size limit, in one transaction each."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, ClassVar, Generic, TypeVar, cast
from uuid import UUID

from django.http import Http404, HttpRequest
from django.utils.translation import gettext as _
from ninja import Schema, Status
from ninja.errors import ValidationError
from ninja.params.functions import Body
from pydantic import BaseModel, Field

from .._internal.cache import owned_cache
from .._internal.generics import LazyAnnotation, type_arguments
from .._internal.i18n import not_found
from ..configuration.settings import class_setting, get_settings
from ..routing.operations import async_variant, post
from ..serialization.pydantic import build_schema
from ..serialization.schemas import patch_type
from .annotations import controller_lookup_type
from .controllers import OUT_SCHEMA, CreateHooks, InT, ModelController, ModelT, OutT
from .writes import write_scope

__all__ = ["BulkCreateMixin", "BulkDelete", "BulkDestroyMixin", "BulkPatch", "BulkUpdateMixin"]

SchemaT = TypeVar("SchemaT", bound=BaseModel)
_Key = int | str | UUID


class _BulkPatchMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return Annotated[_bulk_schema(controller, patch=True), Body()]


class _BulkDeleteMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return Annotated[_bulk_schema(controller, patch=False), Body()]


if TYPE_CHECKING:

    class BulkPatch(Schema, Generic[SchemaT]):
        """``{"pks": [...], "data": {...}}``: apply the same partial update to many objects."""

        pks: builtins.list[_Key]
        data: dict[str, object]

    class BulkDelete(Schema):
        """``{"pks": [...]}``."""

        pks: builtins.list[_Key]

else:

    class _BulkPatchAlias:
        def __getitem__(self, schema: object) -> object:
            return Annotated[schema, _BulkPatchMarker()]

    BulkPatch = _BulkPatchAlias()
    BulkDelete = Annotated[BaseModel, _BulkDeleteMarker()]


def _bulk_schema(controller: type[object], *, patch: bool) -> type[BaseModel]:
    _schemas: dict[type[object], dict[bool, type[BaseModel]]] = owned_cache(
        controller, "bulk_schemas"
    )
    cache = _schemas.setdefault(controller, {})
    if (schema := cache.get(patch)) is not None:
        return schema
    limit = _limit(controller)
    key_type = controller_lookup_type(controller)
    fields: dict[str, tuple[object, object]] = {
        "pks": (builtins.list[key_type], Field(min_length=1, max_length=limit)),  # type: ignore[valid-type]
    }
    if patch:
        input_schema = type_arguments(controller).get(InT)
        fields["data"] = (patch_type(cast(type[BaseModel], input_schema)), ...)
    name = f"{controller.__name__}Bulk{'Patch' if patch else 'Delete'}"
    built = build_schema(name, Schema, fields)
    cache[patch] = built
    return built


def _limit(controller: type[object]) -> int:
    limit: int = class_setting(controller, "bulk_limit", get_settings().bulk_limit)
    return limit


class _BulkBase(ModelController[ModelT], Generic[ModelT]):
    bulk_limit: ClassVar[int] = 100
    """Maximum number of objects per bulk request (``NINJA_DEVX["BULK_LIMIT"]``)."""

    def get_objects(self, request: HttpRequest, lookups: Sequence[object]) -> builtins.list[ModelT]:
        """Fetch objects in request order; 404 if any is missing; object permissions apply."""
        if len(set(lookups)) != len(lookups):
            raise ValidationError(
                [
                    {
                        "type": "duplicate",
                        "loc": ["body", "pks"],
                        "msg": "Duplicate keys are not allowed",
                    }
                ]
            )
        by_key = {
            getattr(instance, self.lookup_field): instance
            for instance in self.scoped_queryset(request).filter(
                **{f"{self.lookup_field}__in": lookups}
            )
        }
        if missing := [lookup for lookup in lookups if lookup not in by_key]:
            raise Http404(f"{not_found(self.get_model())}: {missing}")
        instances = [by_key[lookup] for lookup in lookups]
        for instance in instances:
            self.check_object_permissions(request, instance)
        return instances

    def refresh_many(
        self, request: HttpRequest, instances: Sequence[ModelT]
    ) -> builtins.list[ModelT]:
        order = {instance.pk: index for index, instance in enumerate(instances)}
        fresh = list(self.scoped_queryset(request).filter(pk__in=order))
        found = {instance.pk for instance in fresh}
        if missing := set(order) - found:
            raise Http404(f"{not_found(self.get_model())}: {sorted(map(str, missing))}")
        for instance in fresh:
            self.check_object_permissions(request, instance)
        return sorted(fresh, key=lambda instance: order[instance.pk])


def _check_size(controller: type[object], size: int) -> None:
    limit = _limit(controller)
    if size > limit:
        message = _("At most %(limit)d objects per request, got %(size)d") % {
            "limit": limit,
            "size": size,
        }
        raise ValidationError([{"type": "too_long", "loc": ["body", "payload"], "msg": message}])


class BulkCreateMixin(_BulkBase[ModelT], CreateHooks[ModelT, InT], Generic[ModelT, OutT, InT]):
    """``POST /bulk``: create many objects (validation and signals run per object)."""

    @post("/bulk", response={201: builtins.list[OUT_SCHEMA]})  # type: ignore[valid-type]
    def bulk_create(
        self, request: HttpRequest, payload: builtins.list[InT]
    ) -> Status[builtins.list[ModelT]]:
        _check_size(type(self), len(payload))
        with write_scope(self, request):
            created = [self.perform_create(request, item) for item in payload]
            return Status(201, self.refresh_many(request, created))

    @async_variant(bulk_create)
    async def abulk_create(
        self, request: HttpRequest, payload: builtins.list[InT]
    ) -> Status[builtins.list[ModelT]]:
        await self.aprepare_request(request)
        return await self.run_sync(self.bulk_create, request, payload)


class BulkUpdateMixin(_BulkBase[ModelT], Generic[ModelT, OutT, InT]):
    """``POST /bulk-update``: apply one partial update to many objects."""

    @post("/bulk-update", response=builtins.list[OUT_SCHEMA])  # type: ignore[valid-type]
    def bulk_update(self, request: HttpRequest, payload: BulkPatch[InT]) -> builtins.list[ModelT]:
        with write_scope(self, request):
            instances = self.get_objects(request, payload.pks)
            updated = [
                self.perform_update(request, instance, payload.data) for instance in instances
            ]
            return self.refresh_many(request, updated)

    @async_variant(bulk_update)
    async def abulk_update(
        self, request: HttpRequest, payload: BulkPatch[InT]
    ) -> builtins.list[ModelT]:
        await self.aprepare_request(request)
        return await self.run_sync(self.bulk_update, request, payload)


class BulkDestroyMixin(_BulkBase[ModelT], Generic[ModelT]):
    """``POST /bulk-delete``: delete many objects (``perform_destroy`` per object)."""

    @post("/bulk-delete", response={204: None})
    def bulk_destroy(self, request: HttpRequest, payload: BulkDelete) -> Status[None]:
        with write_scope(self, request):
            for instance in self.get_objects(request, payload.pks):
                self.perform_destroy(request, instance)
        return Status(204, None)

    @async_variant(bulk_destroy)
    async def abulk_destroy(self, request: HttpRequest, payload: BulkDelete) -> Status[None]:
        await self.aprepare_request(request)
        return await self.run_sync(self.bulk_destroy, request, payload)
