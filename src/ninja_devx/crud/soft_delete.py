"""Soft deletion: hide instead of delete, with a restore endpoint.

Nothing is hard-coded: the field, the values that mean "deleted" and "active", who
deleted it, and when, are configured per controller::

    class PostController(SoftDeleteMixin[Post, PostOut], CRUDController[Post, PostOut, PostIn]):
        soft_delete = SoftDelete("deleted_at")                        # nullable DateTimeField
        soft_delete = SoftDelete("is_deleted")                        # BooleanField
        soft_delete = SoftDelete("is_active", deleted=False, active=True)
        soft_delete = SoftDelete("status", deleted="archived", active="published")
        soft_delete = SoftDelete("is_deleted", deleted_at="removed_on", deleted_by="removed_by")

The restore route is ``POST /{pk}/restore``; rename or remove it with ``routes``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import ClassVar, Final, Generic

from django.db.models import BooleanField, DateTimeField, Model, Q, QuerySet, UniqueConstraint
from django.http import HttpRequest
from django.utils import timezone

from .._internal.cache import owned_cache
from ..exceptions import ControllerConfigError
from ..routing.operations import OperationSpec, async_variant, post
from ..security.auth import request_user
from ..stamps import SoftDeletable
from .annotations import Lookup
from .controllers import OUT_SCHEMA, ModelController, ModelT, OutT
from .fields import resolve_field

__all__ = ["SoftDelete", "SoftDeleteMixin", "soft_delete_unique"]


class _Infer(Enum):
    FROM_FIELD = "from the field type"


INFER: Final = _Infer.FROM_FIELD


@dataclass(frozen=True, slots=True)
class SoftDelete:
    """How a model marks deleted rows.

    ``deleted`` is the value (or a zero-argument callable, like ``timezone.now``) written on
    delete; ``active`` is the value of rows that are not deleted, written on restore. Both
    are inferred for a ``BooleanField`` (``True``/``False``) and a nullable
    ``DateTimeField`` (now/``None``); other fields need them explicitly.
    """

    field: str = "deleted_at"
    """The field marking deleted rows."""
    deleted: object = INFER
    """Value written on delete (or a zero-argument callable); inferred for boolean and nullable
    datetime fields."""
    active: object = INFER
    """Value of rows that are not deleted, written on restore; inferred like ``deleted``."""
    deleted_at: str | None = None
    """Also set this ``DateTimeField`` to now on delete (and clear it on restore)."""
    deleted_by: str | None = None
    """Also set this foreign key to the current user on delete (and clear it on restore).
    Defaults to ``"deleted_by"`` for models built on ``SoftDeletable``."""

    def resolved(self, model: type[Model], owner: str) -> _Resolved:
        if self.deleted_by is None and issubclass(model, SoftDeletable):
            return replace(self, deleted_by="deleted_by").resolved(model, owner)
        field = resolve_field(model, self.field)
        deleted, active = self.deleted, self.active
        if isinstance(field, BooleanField):
            deleted = True if deleted is INFER else deleted
            active = (
                (not deleted if isinstance(deleted, bool) else False) if active is INFER else active
            )
        elif isinstance(field, DateTimeField) and field.null:
            deleted = timezone.now if deleted is INFER else deleted
            active = None if active is INFER else active
        elif INFER in (deleted, active):
            raise ControllerConfigError(
                f"{owner}.soft_delete: {model.__name__}.{self.field} is neither a BooleanField nor "
                "a nullable DateTimeField; pass deleted=... and active=... explicitly"
            )
        for extra, kind in ((self.deleted_at, "deleted_at"), (self.deleted_by, "deleted_by")):
            if extra is not None:
                extra_field = resolve_field(model, extra)
                if kind == "deleted_at" and not (
                    isinstance(extra_field, DateTimeField) and extra_field.null
                ):
                    raise ControllerConfigError(
                        f"{owner}.soft_delete.deleted_at must name a nullable DateTimeField"
                    )
                if kind == "deleted_by" and not (extra_field.many_to_one and extra_field.null):
                    raise ControllerConfigError(
                        f"{owner}.soft_delete.deleted_by must name a nullable ForeignKey"
                    )
        return _Resolved(self, deleted, active)


@dataclass(frozen=True, slots=True)
class _Resolved:
    config: SoftDelete
    deleted: object
    active: object

    def active_filter(self) -> dict[str, object]:
        name = self.config.field
        return {f"{name}__isnull": True} if self.active is None else {name: self.active}

    def deleted_values(self, request: HttpRequest) -> dict[str, object]:
        value = self.deleted() if callable(self.deleted) else self.deleted
        values: dict[str, object] = {self.config.field: value}
        if self.config.deleted_at is not None:
            values[self.config.deleted_at] = timezone.now()
        if self.config.deleted_by is not None:
            values[self.config.deleted_by] = request_user(request)
        return values

    def active_values(self) -> dict[str, object]:
        values: dict[str, object] = {self.config.field: self.active}
        if self.config.deleted_at is not None:
            values[self.config.deleted_at] = None
        if self.config.deleted_by is not None:
            values[self.config.deleted_by] = None
        return values


class SoftDeleteMixin(ModelController[ModelT], Generic[ModelT, OutT]):
    """Marks objects deleted instead of deleting them and adds ``POST /{pk}/restore``.

    List it before the CRUD base so its ``perform_destroy`` wins:
    ``class X(SoftDeleteMixin[...], CRUDController[...])``.
    """

    soft_delete: ClassVar[SoftDelete | str] = SoftDelete()
    """A ``SoftDelete`` or just the field name."""
    soft_delete_cascade: ClassVar[Sequence[str]] = ()
    """Related accessor names marked deleted/restored with this object
    (``("comments", "attachments")``). Only the configured marker field is cascaded."""

    @classmethod
    def soft_delete_config(cls) -> _Resolved:
        _resolved: dict[type[object], _Resolved] = owned_cache(cls, "soft_delete_resolved")
        if (cached := _resolved.get(cls)) is None:
            config = cls.soft_delete
            if isinstance(config, str):
                config = SoftDelete(config)
            cached = _resolved[cls] = config.resolved(cls.get_model(), cls.__qualname__)
        return cached

    def scoped_queryset(self, request: HttpRequest) -> QuerySet[ModelT]:
        return self.exclude_deleted(super().scoped_queryset(request))

    def exclude_deleted(self, queryset: QuerySet[ModelT]) -> QuerySet[ModelT]:
        return queryset.filter(**self.soft_delete_config().active_filter())

    def queryset_with_deleted(self, request: HttpRequest) -> QuerySet[ModelT]:
        """``scoped_queryset`` including deleted objects (parent, owner and tenant still apply)."""
        return super().scoped_queryset(request)

    def perform_destroy(self, request: HttpRequest, instance: ModelT) -> None:
        resolved = self.soft_delete_config()
        self._write(instance, resolved.deleted_values(request))
        self._cascade(instance, resolved, deleted=True)

    def perform_restore(self, request: HttpRequest, instance: ModelT) -> ModelT:
        resolved = self.soft_delete_config()
        self._write(instance, resolved.active_values())
        self._cascade(instance, resolved, deleted=False)
        return instance

    def _cascade(self, instance: ModelT, resolved: _Resolved, *, deleted: bool) -> None:
        names = type(self).soft_delete_cascade
        if not names:
            return
        raw = resolved.deleted if deleted else resolved.active
        value = raw() if callable(raw) else raw
        field = resolved.config.field
        for name in names:
            manager = getattr(instance, name)
            manager.all().update(**{field: value})

    @post("/{pk}/restore", response=OUT_SCHEMA)
    def restore(self, request: HttpRequest, pk: Lookup) -> ModelT:
        instance = self.get_object_from(request, self.queryset_with_deleted(request), pk)
        return self.refresh(request, self.perform_restore(request, instance))

    @async_variant(restore)
    async def arestore(self, request: HttpRequest, pk: Lookup) -> ModelT:
        await self.aprepare_request(request)
        return await self.run_sync(self.restore, request, pk)

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        cls.soft_delete_config()  # validates the configuration at startup
        return super().customize_operation(name, spec)

    @staticmethod
    def _write(instance: Model, values: dict[str, object]) -> None:
        for name, value in values.items():
            setattr(instance, name, value)
        instance.save(update_fields=list(values))


def soft_delete_unique(
    model: type[Model],
    *fields: str,
    config: SoftDelete | None = None,
    name: str | None = None,
) -> UniqueConstraint:
    """A partial ``UniqueConstraint`` that only applies to active (not deleted) rows.

    Add it to the model so a deleted row's value can be reused::

        class Post(models.Model):
            slug = models.SlugField()
            deleted_at = models.DateTimeField(null=True)

            class Meta:
                constraints = [soft_delete_unique(Post, "slug")]

    :param model: The model (used to resolve the marker field and its active value).
    :param fields: The constrained fields.
    :param config: The ``SoftDelete`` configuration (default: ``deleted_at``).
    :param name: Constraint name (default derived from the table and fields).
    """
    if not fields:
        raise ControllerConfigError("soft_delete_unique needs at least one field")
    resolved = (config or SoftDelete()).resolved(model, "soft_delete_unique")
    condition = Q(**resolved.active_filter())
    label = name or f"uniq_active_{model._meta.db_table}_{'_'.join(fields)}"
    return UniqueConstraint(fields=list(fields), condition=condition, name=label)
