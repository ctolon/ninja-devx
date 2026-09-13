from __future__ import annotations

from typing import TYPE_CHECKING

import django
from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.forms import ModelForm, ValidationError
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from .maintenance import retry_deliveries
from .models import OutboxEvent, WebhookDelivery, WebhookEndpoint
from .network import UnsafeURL, URLPolicy, check_url
from .signing import generate_secret

if TYPE_CHECKING:
    _EndpointBase = admin.ModelAdmin[WebhookEndpoint]
    _DeliveryBase = admin.ModelAdmin[WebhookDelivery]
    _EventBase = admin.ModelAdmin[OutboxEvent]
    _EndpointForm = ModelForm[WebhookEndpoint]
else:
    _EndpointBase = _DeliveryBase = _EventBase = admin.ModelAdmin
    _EndpointForm = ModelForm


def _url_field() -> forms.URLField:
    if django.VERSION >= (5, 0):  # no deprecation warning about the default scheme
        return forms.URLField(max_length=500, assume_scheme="https")
    return forms.URLField(max_length=500)


class WebhookEndpointForm(_EndpointForm):
    url_policy = URLPolicy()
    url = _url_field()

    class Meta:
        model = WebhookEndpoint
        fields = ("url", "description", "events", "owner", "tenant_key", "is_active")

    def clean_url(self) -> str:
        url = str(self.cleaned_data["url"])
        try:
            check_url(url, self.url_policy)
        except UnsafeURL as exc:
            raise ValidationError(str(exc)) from exc
        return url


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(_EndpointBase):
    """The signing secret is never displayed, except once in a message when created or
    rotated."""

    form = WebhookEndpointForm
    list_display = ("url", "owner", "events", "is_active", "failing_since", "created")
    list_filter = ("is_active",)
    list_select_related = ("owner",)
    search_fields = ("url", "description")
    raw_id_fields = ("owner",)
    readonly_fields = ("failing_since", "disabled_reason", "created")
    actions = ("rotate_secret", "enable")

    def save_model(
        self, request: HttpRequest, obj: WebhookEndpoint, form: object, change: bool
    ) -> None:
        if not change:
            raw = generate_secret()
            obj.set_secret(raw)
            self.message_user(
                request,
                _("Signing secret (shown once): %(secret)s") % {"secret": raw},
                messages.WARNING,
            )
        super().save_model(request, obj, form, change)

    @admin.action(description=_("Rotate the signing secret"))
    def rotate_secret(self, request: HttpRequest, queryset: QuerySet[WebhookEndpoint]) -> None:
        for endpoint in queryset:
            raw = generate_secret()
            endpoint.set_secret(raw)
            endpoint.save(update_fields=["secret"])
            self.message_user(
                request,
                _("New secret for %(url)s (shown once): %(secret)s")
                % {"url": endpoint.url, "secret": raw},
                messages.WARNING,
            )

    @admin.action(description=_("Enable and clear failures"))
    def enable(self, request: HttpRequest, queryset: QuerySet[WebhookEndpoint]) -> None:
        queryset.update(is_active=True, failing_since=None, disabled_reason="")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(_DeliveryBase):
    list_display = (
        "id",
        "event",
        "endpoint",
        "status",
        "attempts",
        "last_status_code",
        "next_attempt_at",
        "delivered_at",
    )
    list_filter = ("status",)
    list_select_related = ("event", "endpoint")
    search_fields = ("endpoint__url", "event__event_type", "last_error")
    readonly_fields = (
        "event",
        "endpoint",
        "attempts",
        "last_status_code",
        "last_error",
        "delivered_at",
    )
    actions = ("retry",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    @admin.action(description=_("Send again"))
    def retry(self, request: HttpRequest, queryset: QuerySet[WebhookDelivery]) -> None:
        count = retry_deliveries(queryset)
        self.message_user(request, _("%(count)d delivery(ies) queued.") % {"count": count})


@admin.register(OutboxEvent)
class OutboxEventAdmin(_EventBase):
    list_display = ("id", "event_type", "created")
    list_filter = ("event_type",)
    search_fields = ("id", "event_type")
    date_hierarchy = "created"

    def get_readonly_fields(
        self, request: HttpRequest, obj: OutboxEvent | None = None
    ) -> list[str]:
        return ["id", "event_type", "payload", "created"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: OutboxEvent | None = None) -> bool:
        return False
