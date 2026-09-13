from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import APIKey

if TYPE_CHECKING:
    _Base = admin.ModelAdmin[APIKey]
else:
    _Base = admin.ModelAdmin


@admin.register(APIKey)
class APIKeyAdmin(_Base):
    """Keys are created with ``manage.py devx_apikey`` or the API (the raw key is shown
    once there); the admin inspects, edits scopes and limits, and revokes."""

    list_display = ("name", "prefix", "user", "rate_limit", "created", "last_used_at", "state")
    list_filter = ("revoked_at", "expires_at")
    list_select_related = ("user",)
    search_fields = ("name", "prefix", "user__username")
    raw_id_fields = ("user",)
    exclude = ("hashed_secret",)
    readonly_fields = ("prefix", "user", "created", "last_used_at", "revoked_at")
    actions = ("revoke",)

    @admin.display(description=_("state"))
    def state(self, key: APIKey) -> str:
        if key.revoked_at is not None:
            return str(_("revoked"))
        expires: object = key.expires_at
        if isinstance(expires, datetime) and expires <= timezone.now():
            return str(_("expired"))
        return str(_("active"))

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    @admin.action(description=_("Revoke selected API keys"))
    def revoke(self, request: HttpRequest, queryset: QuerySet[APIKey]) -> None:
        count = queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        self.message_user(
            request, _("%(count)d key(s) revoked.") % {"count": count}, messages.SUCCESS
        )
