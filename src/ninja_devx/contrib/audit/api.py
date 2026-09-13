"""Read-only audit log endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, ClassVar, Generic
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from ninja import FilterLookup, FilterSchema, Schema
from ninja.pagination import PageNumberPagination, PaginationBase, paginate
from pydantic import Field

from ...configuration.settings import class_setting
from ...crud.annotations import Lookup
from ...crud.controllers import ModelController, ModelT, ReadOnlyModelController
from ...routing.controller import ControllerOptions
from ...routing.operations import get
from ...security.permissions import Also, IsStaff
from .models import AuditEntry

__all__ = ["AuditEntryOut", "AuditFilters", "AuditHistoryMixin", "AuditLogController"]


class AuditEntryOut(Schema):
    id: int
    created: datetime
    action: str
    actor_id: int | str | UUID | None = Field(
        None, description="The user, or null (deleted user or a job)."
    )
    actor_label: str
    model: str | None = Field(None, description="`app_label.model` of the object.")
    object_pk: str
    object_repr: str
    changes: dict[str, list[object]] = Field(description="`{field: [old, new]}`.")
    metadata: dict[str, object]
    request_id: str
    method: str
    path: str
    ip_address: str | None

    @staticmethod
    def resolve_model(obj: AuditEntry) -> str | None:
        content_type: ContentType | None = obj.content_type
        if content_type is None:
            return None
        return f"{content_type.app_label}.{content_type.model}"


class AuditFilters(FilterSchema):
    """Query parameters of ``GET /`` on ``AuditLogController``."""

    action: str | None = None
    """``create``, ``update``, ``delete`` or a custom action."""
    actor_id: int | str | UUID | None = None
    """Entries by this user."""
    model: str | None = Field(None, description="`app_label.model`.")
    """Entries about this model: ``app_label.model``."""
    object_pk: str | None = None
    """Entries about this object (with ``model``)."""
    request_id: str | None = None
    """Everything recorded during one request."""
    since: Annotated[datetime | None, FilterLookup("created__gte")] = None
    """Entries at or after this time."""
    until: Annotated[datetime | None, FilterLookup("created__lt")] = None
    """Entries before this time."""

    def filter_model(self, value: str | None) -> Q:
        if not value or "." not in value:
            return Q()
        app_label, model = value.lower().split(".", 1)
        return Q(content_type__app_label=app_label, content_type__model=model)


class AuditLogController(ReadOnlyModelController[AuditEntry, AuditEntryOut]):
    """``GET /`` (filterable) and ``GET /{pk}``; staff only by default."""

    options = ControllerOptions(permissions=[IsStaff()], tags=["audit"])
    filter_schema = AuditFilters
    ordering_fields = ("created",)
    pagination_options: ClassVar[Mapping[str, object]] = MappingProxyType(
        {"page_size": 50, "max_page_size": 100}
    )

    @classmethod
    def pagination(cls) -> type[PaginationBase] | None:
        return class_setting(cls, "pagination_class", PageNumberPagination)

    def get_queryset(self, request: HttpRequest) -> QuerySet[AuditEntry]:
        return AuditEntry.objects.select_related("content_type").order_by("-created", "-pk")


class AuditHistoryMixin(ModelController[ModelT], Generic[ModelT]):
    """``GET /{pk}/history``: the object's audit entries, newest first.

    The object is loaded like ``retrieve`` (scoping, object permissions: 404/403), so
    staff access is additionally required by default. An explicit ``routes["history"]``
    permission override can implement a project-specific audit-reader policy.
    """

    @get(
        "/{pk}/history",
        response=list[AuditEntryOut],
        summary="Change history of the object",
        permissions=Also(IsStaff()),
        decorators=[paginate(PageNumberPagination, page_size=50, max_page_size=100)],
    )
    def history(self, request: HttpRequest, pk: Lookup) -> QuerySet[AuditEntry]:
        instance = self.get_object(request, pk)
        alias = instance._state.db
        content_type = ContentType.objects.db_manager(alias).get_for_model(type(instance))
        return (
            AuditEntry.objects.using(alias)
            .filter(content_type=content_type, object_pk=str(instance.pk))
            .select_related("content_type")
            .order_by("-created", "-pk")
        )
