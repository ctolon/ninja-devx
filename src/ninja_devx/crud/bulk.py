"""Bulk create, update and delete with a size limit, in one transaction each."""

from __future__ import annotations

import builtins
import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Generic, TypeVar, cast
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponseBase
from django.utils.translation import gettext as _
from ninja import Schema, Status
from ninja.decorators import decorate_view
from ninja.errors import HttpError, ValidationError
from ninja.params.functions import Body
from pydantic import BaseModel, Field

from .._internal.cache import owned_cache
from .._internal.generics import LazyAnnotation, type_arguments
from .._internal.i18n import not_found
from .._internal.types import ViewDecorator, ViewFunction
from ..configuration.settings import class_setting, get_settings
from ..layers.errors import DomainError
from ..routing.operations import async_variant, post
from ..serialization.pydantic import build_schema
from ..serialization.schemas import patch_type
from .annotations import controller_lookup_type
from .controllers import OUT_SCHEMA, CreateHooks, InT, ModelController, ModelT, OutT
from .persistence import changed_fields, validation_failed
from .writes import write_scope

__all__ = [
    "BulkCreateMixin",
    "BulkDelete",
    "BulkDestroyMixin",
    "BulkErrorDetail",
    "BulkPatch",
    "BulkResultOut",
    "BulkUpdateMixin",
]

SchemaT = TypeVar("SchemaT", bound=BaseModel)
_Key = int | str | UUID


class _BulkPatchMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return Annotated[_bulk_schema(controller, patch=True), Body()]


class _BulkDeleteMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return Annotated[_bulk_schema(controller, patch=False), Body()]


if TYPE_CHECKING:
    from .._internal.types import ResponseSpec

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


class BulkErrorDetail(Schema):
    """One item of a bulk 207 entry's ``errors``, in Ninja's validation error shape."""

    type: str
    """Ninja's validation error type (``"missing"``, ``"value_error"``, ...)."""
    loc: builtins.list[int | str]
    """Field path within the item, as Ninja reports it (without the item's own index)."""
    msg: str
    """Human-readable message."""


class _BulkResultMarker(LazyAnnotation):
    def resolve(self, annotation: object, controller: type[object]) -> object:
        return _bulk_result_schema(controller)


BulkResultOut = Annotated[BaseModel, _BulkResultMarker()]
"""``{"results": [{"index", "status", "data"} | {"index", "status", "errors"}]}``: the body
of a partial-success (207) bulk response, its ``data`` typed like the controller's output."""


def _bulk_result_schema(controller: type[object]) -> type[BaseModel]:
    """``{"results": [...]}`` typed with the controller's own output schema (cached)."""
    _schemas: dict[type[object], type[BaseModel]] = owned_cache(controller, "bulk_result_schemas")
    if (schema := _schemas.get(controller)) is not None:
        return schema
    output_schema = cast("type[BaseModel]", type_arguments(controller).get(OutT))
    item = build_schema(
        f"{controller.__name__}BulkItem",
        Schema,
        {
            "index": (int, ...),
            "status": (int, ...),
            "data": (output_schema | None, None),
            "errors": (builtins.list[BulkErrorDetail] | None, None),
        },
    )
    built = build_schema(
        f"{controller.__name__}BulkResult",
        Schema,
        {"results": (builtins.list[item], ...)},  # type: ignore[valid-type]
    )
    _schemas[controller] = built
    return built


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
        _reject_duplicates(lookups)
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


def _reject_duplicates(lookups: Sequence[object]) -> None:
    if len(set(lookups)) != len(lookups):
        raise ValidationError(
            [{"type": "duplicate", "loc": ["body", "pks"], "msg": "Duplicate keys are not allowed"}]
        )


def _bulk_error(exc: Exception) -> tuple[int, builtins.list[dict[str, object]]] | None:
    """``(status, errors)`` for an exception a bulk partial item may fail with, or ``None``
    when it should abort the whole request instead of becoming a 207 entry."""
    if isinstance(exc, DjangoValidationError):
        failure = validation_failed(exc)
        errors: builtins.list[dict[str, object]] = [
            {"type": failure.code, "loc": ["body", field] if field else ["body"], "msg": message}
            for field, messages in failure.errors.items()
            for message in messages
        ] or [{"type": failure.code, "loc": ["body"], "msg": failure.message}]
        return 422, errors
    if isinstance(exc, IntegrityError):
        return 422, [{"type": "integrity_error", "loc": ["body"], "msg": str(exc)}]
    if isinstance(exc, DomainError):
        return exc.http_status, [{"type": exc.code, "loc": ["body"], "msg": exc.message}]
    if isinstance(exc, Http404):
        return 404, [{"type": "not_found", "loc": ["body"], "msg": str(exc)}]
    if isinstance(exc, HttpError):
        return exc.status_code, [{"type": "http_error", "loc": ["body"], "msg": str(exc)}]
    return None


_FAILED_ATTR: Final = "_ninja_devx_bulk_failed"


def _remember_bulk_failed(request: HttpRequest, count: int) -> None:
    request.__dict__[_FAILED_ATTR] = count


def _apply_failed_header(request: HttpRequest, response: HttpResponseBase) -> HttpResponseBase:
    count: object = request.__dict__.get(_FAILED_ATTR)
    if isinstance(count, int):
        response["X-Bulk-Failed"] = str(count)
    return response


def _wrap_run(run: Callable[..., object]) -> Callable[..., object]:
    if inspect.iscoroutinefunction(run):

        async def arun(request: HttpRequest, *args: object, **kwargs: object) -> object:
            awaitable = cast("Callable[..., Awaitable[HttpResponseBase]]", run)
            response = await awaitable(request, *args, **kwargs)
            return _apply_failed_header(request, response)

        return arun

    def srun(request: HttpRequest, *args: object, **kwargs: object) -> object:
        response = cast("Callable[..., HttpResponseBase]", run)(request, *args, **kwargs)
        return _apply_failed_header(request, response)

    return srun


def _bulk_response_headers() -> ViewDecorator:
    """Adds ``X-Bulk-Failed`` (a partial-success item count) set with ``_remember_bulk_failed``."""

    def decorator(handler: ViewFunction) -> ViewFunction:
        return decorate_view(_wrap_run)(handler)

    return decorator


class BulkCreateMixin(_BulkBase[ModelT], CreateHooks[ModelT, InT], Generic[ModelT, OutT, InT]):
    """``POST /bulk``: create many objects (validation and signals run per object)."""

    bulk_partial: ClassVar[bool] = False
    """Create each item in its own savepoint: failures become 207 entries (``results``)
    instead of rolling back the whole request."""

    @post(
        "/bulk",
        response=cast(
            "ResponseSpec",
            {201: builtins.list[OUT_SCHEMA], 207: BulkResultOut},  # type: ignore[valid-type]
        ),
        decorators=(_bulk_response_headers(),),
    )
    def bulk_create(
        self, request: HttpRequest, payload: builtins.list[InT]
    ) -> Status[builtins.list[ModelT]] | Status[dict[str, object]]:
        _check_size(type(self), len(payload))
        if type(self).bulk_partial:
            return self._bulk_create_partial(request, payload)
        with write_scope(self, request):
            created = [self.perform_create(request, item) for item in payload]
            return Status(201, self.refresh_many(request, created))

    def _bulk_create_partial(
        self, request: HttpRequest, payload: builtins.list[InT]
    ) -> Status[dict[str, object]]:
        alias = self.write_database(request)
        results: builtins.list[dict[str, object]] = []
        failed = 0
        with write_scope(self, request):
            for index, item in enumerate(payload):
                try:
                    with transaction.atomic(using=alias):
                        created = self.refresh(request, self.perform_create(request, item))
                except Exception as exc:
                    outcome = _bulk_error(exc)
                    if outcome is None:
                        raise
                    status, errors = outcome
                    failed += 1
                    results.append({"index": index, "status": status, "errors": errors})
                    continue
                results.append({"index": index, "status": 201, "data": created})
        _remember_bulk_failed(request, failed)
        return Status(207, {"results": results})

    @async_variant(bulk_create)
    async def abulk_create(
        self, request: HttpRequest, payload: builtins.list[InT]
    ) -> Status[builtins.list[ModelT]] | Status[dict[str, object]]:
        await self.aprepare_request(request)
        return await self.run_sync(self.bulk_create, request, payload)


class BulkUpdateMixin(_BulkBase[ModelT], Generic[ModelT, OutT, InT]):
    """``POST /bulk-update``: apply one partial update to many objects."""

    bulk_partial: ClassVar[bool] = False
    """Update each item in its own savepoint: an unknown pk or a per-item failure becomes a
    207 entry (``results``) instead of rolling back the whole request."""

    @post(
        "/bulk-update",
        response=cast(
            "ResponseSpec",
            {200: builtins.list[OUT_SCHEMA], 207: BulkResultOut},  # type: ignore[valid-type]
        ),
        decorators=(_bulk_response_headers(),),
    )
    def bulk_update(
        self, request: HttpRequest, payload: BulkPatch[InT]
    ) -> Status[builtins.list[ModelT]] | Status[dict[str, object]]:
        if type(self).bulk_partial:
            return self._bulk_update_partial(request, payload)
        with write_scope(self, request):
            instances = self.get_objects(request, payload.pks)
            changes = {
                instance.pk: changed_fields(instance, payload.data) for instance in instances
            }
            updated = [
                self.perform_update(request, instance, payload.data) for instance in instances
            ]
            refreshed = self.refresh_many(request, updated)
            for instance in refreshed:
                if changes.get(instance.pk):
                    self.on_change(request, instance, changes[instance.pk])
            return Status(200, refreshed)

    def _bulk_update_partial(
        self, request: HttpRequest, payload: BulkPatch[InT]
    ) -> Status[dict[str, object]]:
        _reject_duplicates(payload.pks)
        alias = self.write_database(request)
        results: builtins.list[dict[str, object]] = []
        failed = 0
        with write_scope(self, request):
            for index, lookup in enumerate(payload.pks):
                instance = (
                    self.scoped_queryset(request)
                    .using(alias)
                    .filter(**{self.lookup_field: lookup})
                    .first()
                )
                if instance is None:
                    failed += 1
                    results.append(
                        {
                            "index": index,
                            "status": 404,
                            "errors": [
                                {
                                    "type": "not_found",
                                    "loc": ["body", "pks", index],
                                    "msg": not_found(self.get_model()),
                                }
                            ],
                        }
                    )
                    continue
                try:
                    with transaction.atomic(using=alias):
                        self.check_object_permissions(request, instance)
                        changes = changed_fields(instance, payload.data)
                        updated = self.refresh(
                            request, self.perform_update(request, instance, payload.data)
                        )
                        if changes:
                            self.on_change(request, updated, changes)
                except Exception as exc:
                    outcome = _bulk_error(exc)
                    if outcome is None:
                        raise
                    status, errors = outcome
                    failed += 1
                    results.append({"index": index, "status": status, "errors": errors})
                    continue
                results.append({"index": index, "status": 200, "data": updated})
        _remember_bulk_failed(request, failed)
        return Status(207, {"results": results})

    @async_variant(bulk_update)
    async def abulk_update(
        self, request: HttpRequest, payload: BulkPatch[InT]
    ) -> Status[builtins.list[ModelT]] | Status[dict[str, object]]:
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
