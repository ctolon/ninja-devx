"""Managing webhook endpoints over the API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import ClassVar, cast
from uuid import UUID

from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja import Schema, Status
from ninja.errors import HttpError, ValidationError
from pydantic import AnyHttpUrl, Field

from ...crud.annotations import Lookup
from ...crud.controllers import CRUDController
from ...routing.controller import ControllerOptions
from ...routing.operations import get, post
from ...security.permissions import IsAuthenticated
from .maintenance import retry_deliveries
from .models import OutboxEvent, WebhookDelivery, WebhookEndpoint
from .network import UnsafeURL, URLPolicy, check_url
from .outbox import event_matches
from .secrets import encrypt_secret
from .signing import generate_secret

__all__ = [
    "WebhookDeliveryOut",
    "WebhookEndpointController",
    "WebhookEndpointCreated",
    "WebhookEndpointIn",
    "WebhookEndpointOut",
]


class WebhookEndpointIn(Schema):
    url: AnyHttpUrl
    description: str = Field("", max_length=200)
    events: list[str] = Field(min_length=1, description='Event types or patterns: "order.*", "*".')
    is_active: bool = True


class WebhookEndpointOut(Schema):
    id: int
    url: str
    description: str
    events: list[str]
    is_active: bool
    failing_since: datetime | None
    disabled_reason: str
    created: datetime


class WebhookEndpointCreated(WebhookEndpointOut):
    secret: str = Field(
        alias="signing_secret", description="Signing secret. Shown only on creation and rotation."
    )


class WebhookDeliveryOut(Schema):
    id: int
    event_id: UUID
    event_type: str = Field(alias="event.event_type")
    status: str
    attempts: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str
    delivered_at: datetime | None


class WebhookEndpointController(
    CRUDController[WebhookEndpoint, WebhookEndpointOut, WebhookEndpointIn]
):
    """The user's endpoints: CRUD, deliveries, retry, ping and secret rotation."""

    options = ControllerOptions(permissions=[IsAuthenticated()], tags=["webhooks"])
    owner_field = "owner"
    scope_queryset_to_owner = True
    url_policy: ClassVar[URLPolicy] = URLPolicy()
    """Which URLs may be registered (HTTPS, public addresses by default). Pass the same
    policy to the delivery worker."""
    available_events: ClassVar[Sequence[str] | None] = None
    """Event types clients may subscribe to (``None``: any); patterns must match one."""

    def get_queryset(self, request: HttpRequest) -> QuerySet[WebhookEndpoint]:
        queryset = WebhookEndpoint.objects.all()
        # A personal API must not expose endpoints created in a tenant context.
        return queryset.filter(tenant_key="") if type(self).tenant_field is None else queryset

    def perform_create(self, request: HttpRequest, payload: WebhookEndpointIn) -> WebhookEndpoint:
        self._check_events(payload.events)
        self._check_url(str(payload.url))
        data = {**payload.model_dump(mode="json"), "secret": encrypt_secret(generate_secret())}
        return self.get_service(request).create({**data, **self.context_data(request)})

    def perform_update(
        self, request: HttpRequest, instance: WebhookEndpoint, data: Mapping[str, object]
    ) -> WebhookEndpoint:
        values = dict(data)
        if "events" in values:
            self._check_events(values["events"])
        if "url" in values:
            url = str(values["url"])
            self._check_url(url)
            values["url"] = url
        if values.get("is_active") is True:
            values["failing_since"] = None
            values["disabled_reason"] = ""
        return self.get_service(request).update(instance, values)

    def refresh(self, request: HttpRequest, instance: WebhookEndpoint) -> WebhookEndpoint:
        return instance

    @post("/", response={201: WebhookEndpointCreated})
    def create(self, request: HttpRequest, payload: WebhookEndpointIn) -> Status[WebhookEndpoint]:
        return Status(201, self.perform_create(request, payload))

    @post(
        "/{pk}/rotate-secret", response=WebhookEndpointCreated, summary="Rotate the signing secret"
    )
    def rotate_secret(self, request: HttpRequest, pk: Lookup) -> WebhookEndpoint:
        endpoint = self.get_object(request, pk)
        endpoint.set_secret(generate_secret())
        endpoint.save(update_fields=["secret"])
        return endpoint

    @get("/{pk}/deliveries", response=list[WebhookDeliveryOut], summary="Recent deliveries")
    def deliveries(self, request: HttpRequest, pk: Lookup) -> list[WebhookDelivery]:
        endpoint = self.get_object(request, pk)
        return list(
            WebhookDelivery.objects.filter(endpoint=endpoint)
            .select_related("event")
            .order_by("-id")[:100]
        )

    @post(
        "/{pk}/deliveries/{delivery_id}/retry",
        response=WebhookDeliveryOut,
        summary="Send a delivery again",
    )
    def retry_delivery(self, request: HttpRequest, pk: Lookup, delivery_id: int) -> WebhookDelivery:
        endpoint = self.get_object(request, pk)
        delivery = (
            WebhookDelivery.objects.select_related("event")
            .filter(endpoint=endpoint, pk=delivery_id)
            .first()
        )
        if delivery is None:
            raise HttpError(404, _("Delivery not found"))
        changed = retry_deliveries(
            WebhookDelivery.objects.using(delivery._state.db).filter(pk=delivery.pk)
        )
        if not changed:
            raise HttpError(409, _("Delivery is currently claimed by a worker"))
        delivery.refresh_from_db()
        return delivery

    @post("/{pk}/ping", response={202: WebhookDeliveryOut}, summary="Queue a test event")
    def ping(self, request: HttpRequest, pk: Lookup) -> Status[WebhookDelivery]:
        endpoint = self.get_object(request, pk)
        event = OutboxEvent.objects.create(
            event_type="webhook.ping",
            payload={"endpoint_id": endpoint.pk},
            audience={"owner": str(endpoint.owner_id), "tenant_key": endpoint.tenant_key},
        )
        delivery = WebhookDelivery.objects.create(
            event=event, endpoint=endpoint, next_attempt_at=timezone.now()
        )
        return Status(202, delivery)

    def _check_url(self, url: str) -> None:
        try:
            check_url(url, type(self).url_policy)
        except UnsafeURL as exc:
            raise ValidationError(
                [{"type": "url_unsafe", "loc": ["body", "payload", "url"], "msg": str(exc)}]
            ) from exc

    def _check_events(self, events: object) -> None:
        available = type(self).available_events
        if available is None or not isinstance(events, list):
            return
        patterns = [str(item) for item in cast("list[object]", events)]
        unknown = [
            pattern
            for pattern in patterns
            if not any(event_matches([pattern], name) for name in available)
        ]
        if unknown:
            raise HttpError(
                422,
                _("Unknown events %(unknown)s; available: %(available)s")
                % {"unknown": ", ".join(unknown), "available": ", ".join(available)},
            )
