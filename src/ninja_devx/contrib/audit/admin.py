from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin
from django.http import HttpRequest

from .models import AuditEntry

if TYPE_CHECKING:
    _Base = admin.ModelAdmin[AuditEntry]
else:
    _Base = admin.ModelAdmin


@admin.register(AuditEntry)
class AuditEntryAdmin(_Base):
    """Read-only: entries are evidence. Superusers may delete them (retention)."""

    list_display = ("created", "action", "actor_label", "content_type", "object_repr", "request_id")
    list_filter = ("action", "content_type")
    list_select_related = ("content_type",)
    search_fields = ("object_pk", "object_repr", "actor_label", "request_id")
    date_hierarchy = "created"

    def get_readonly_fields(self, request: HttpRequest, obj: AuditEntry | None = None) -> list[str]:
        return [field.name for field in AuditEntry._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: AuditEntry | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: AuditEntry | None = None) -> bool:
        return bool(getattr(request.user, "is_superuser", False))
