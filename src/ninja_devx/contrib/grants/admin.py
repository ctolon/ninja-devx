from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin

from .models import ObjectGrant

if TYPE_CHECKING:
    _Base = admin.ModelAdmin[ObjectGrant]
else:
    _Base = admin.ModelAdmin


@admin.register(ObjectGrant)
class ObjectGrantAdmin(_Base):
    list_display = ("permission", "content_type", "object_pk", "user", "group", "created")
    list_filter = ("content_type",)
    list_select_related = ("permission", "content_type", "user", "group")
    search_fields = ("object_pk", "permission__codename")
    raw_id_fields = ("user", "group")
    date_hierarchy = "created"
