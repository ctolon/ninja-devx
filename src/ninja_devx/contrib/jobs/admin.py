from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin
from django.http import HttpRequest

from .models import Job

if TYPE_CHECKING:
    _Base = admin.ModelAdmin[Job]
else:
    _Base = admin.ModelAdmin


@admin.register(Job)
class JobAdmin(_Base):
    """Jobs are created with ``start_job`` and run by a worker; the admin inspects progress
    and results, and ``manage.py devx_jobs retry <id>`` replays a failed one."""

    list_display = ("name", "status", "created_by", "progress", "attempts", "created", "finished")
    list_filter = ("status",)
    list_select_related = ("created_by",)
    search_fields = ("name", "id", "created_by__username")
    raw_id_fields = ("created_by",)
    readonly_fields = (
        "id",
        "function",
        "arguments",
        "created_by",
        "created",
        "started",
        "finished",
        "attempts",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
