"""Publishing events and delivering them to endpoints."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol, cast

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.core.serializers.json import DjangoJSONEncoder
from django.db import router, transaction
from django.db.models import Model
from django.utils import timezone

from ...layers.tasks import Enqueueable, TaskQueue
from .models import OutboxEvent, WebhookDelivery, WebhookEndpoint
from .network import SafeHTTPTransport, URLPolicy
from .signing import signature_headers

__all__ = [
    "RETRY_SCHEDULE",
    "DeliveryReport",
    "Transport",
    "apublish",
    "deliver_due",
    "deliver_event",
    "event_matches",
    "publish",
]

RETRY_SCHEDULE: Final = (
    timedelta(seconds=5),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=5),
    timedelta(hours=10),
    timedelta(hours=10),
)
"""Delays before the retries (Standard Webhooks' schedule): 8 attempts over ~27 hours."""


def event_matches(patterns: Sequence[str], event_type: str) -> bool:
    """``"*"``, an exact type, or a ``"order.*"`` prefix.

    :param patterns: An endpoint's subscriptions.
    :param event_type: The published event type.
    """
    return any(
        pattern in {"*", event_type}
        or (pattern.endswith(".*") and event_type.startswith(pattern[:-1]))
        for pattern in patterns
    )


def _patterns(endpoint: WebhookEndpoint) -> list[str]:
    events: object = endpoint.events
    items = cast("list[object]", events) if isinstance(events, list) else []
    return [str(item) for item in items]


def publish(
    event_type: str,
    payload: Mapping[str, object],
    *,
    owner: Model | None = None,
    tenant_key: str = "",
    broadcast: bool = False,
    using: str | None = None,
    queue: TaskQueue | None = None,
    task: Enqueueable[[str, str]] | None = None,
) -> OutboxEvent:
    """Store an event and a pending delivery per subscribed endpoint, in the current
    transaction (``DjangoJSONEncoder`` serializes dates, decimals and UUIDs).

    Without ``queue`` a worker (``devx_webhooks deliver``) sends it. With a queue, delivery
    is also enqueued right away, after commit with ``OnCommitTaskQueue``; the worker still
    retries failures.

    :param event_type: Dotted event name (``"order.created"``); endpoints subscribe to it.
    :param payload: The event data, sent as ``data`` in the request body.
    :param owner: Only this user's endpoints, within ``tenant_key``.
    :param tenant_key: Server-controlled tenant scope; empty means personal endpoints.
    :param broadcast: Explicitly send to every subscribed endpoint; cannot combine with a target.
    :param using: Database alias shared by business writes and the outbox.
    :param queue: Where to enqueue the delivery (``OnCommitTaskQueue()``).
    :param task: The task receiving the event id and database alias. Default:
        ``deliver_event_task`` (Django 6.0+); pass your Celery/RQ wrapper otherwise.
    """
    if broadcast and (owner is not None or tenant_key):
        raise ValueError("broadcast cannot be combined with owner or tenant_key")
    if not broadcast and owner is None and not tenant_key:
        raise ValueError("publish requires owner, tenant_key or explicit broadcast=True")
    if owner is not None and owner.pk is None:
        raise ValueError("publish owner must be saved")
    if len(tenant_key) > 200:
        raise ValueError("tenant_key exceeds 200 characters")
    if not event_type or len(event_type) > 100:
        raise ValueError("event_type must contain 1 to 100 characters")
    database = using or router.db_for_write(OutboxEvent)
    if owner is not None and owner._state.db not in {None, database}:
        raise ValueError("publish owner and outbox must use the same database")
    queued_task = (task or _default_task()) if queue is not None else None
    data: object = json.loads(json.dumps(dict(payload), cls=DjangoJSONEncoder))
    audience = {
        "owner": str(owner.pk) if owner is not None else None,
        "tenant_key": tenant_key,
        "broadcast": broadcast,
    }
    with transaction.atomic(using=database):
        event = OutboxEvent.objects.using(database).create(
            event_type=event_type, payload=data, audience=audience
        )
        now = timezone.now()
        endpoints = WebhookEndpoint.objects.using(database).filter(is_active=True)
        if not broadcast:
            endpoints = endpoints.filter(tenant_key=tenant_key)
            if owner is not None:
                endpoints = endpoints.filter(owner=owner)
        batch: list[WebhookDelivery] = []
        for endpoint in endpoints.iterator(chunk_size=500):
            if event_matches(_patterns(endpoint), event_type):
                batch.append(WebhookDelivery(event=event, endpoint=endpoint, next_attempt_at=now))
            if len(batch) == 500:
                WebhookDelivery.objects.using(database).bulk_create(batch, batch_size=500)
                batch.clear()
        if batch:
            WebhookDelivery.objects.using(database).bulk_create(batch, batch_size=500)
        if queue is not None and queued_task is not None:
            transaction.on_commit(
                lambda: queue.enqueue(queued_task, str(event.pk), database), using=database
            )
    return event


def _default_task() -> Enqueueable[[str, str]]:
    from .tasks import deliver_event_task

    if deliver_event_task is None:
        raise ImproperlyConfigured(
            "publish(queue=...) needs django.tasks (Django 6.0+) or task=<your task>"
        )
    return deliver_event_task


async def apublish(
    event_type: str,
    payload: Mapping[str, object],
    *,
    owner: Model | None = None,
    tenant_key: str = "",
    broadcast: bool = False,
    using: str | None = None,
    queue: TaskQueue | None = None,
    task: Enqueueable[[str, str]] | None = None,
) -> OutboxEvent:
    return await sync_to_async(publish)(
        event_type,
        payload,
        owner=owner,
        tenant_key=tenant_key,
        broadcast=broadcast,
        using=using,
        queue=queue,
        task=task,
    )


def deliver_event(event_id: str, *, using: str | None = None) -> DeliveryReport:
    """Send the due deliveries of one event now (what enqueued tasks run).

    :param event_id: The ``OutboxEvent`` primary key.
    :param using: Database alias containing the outbox event.
    """
    return deliver_due(event_id=event_id, limit=1000, using=using)


class Transport(Protocol):
    """Sends one request; returns the status code or raises ``OSError``."""

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> int: ...


@dataclass(slots=True)
class DeliveryReport:
    succeeded: int = 0
    retrying: int = 0
    failed: int = 0
    disabled_endpoints: int = 0
    lost_claims: int = 0


def body_of(event: OutboxEvent) -> bytes:
    """The JSON request body: ``{"type", "timestamp", "data"}``."""
    created: object = event.created
    stamp = created.isoformat() if isinstance(created, datetime) else None
    content = {"type": event.event_type, "timestamp": stamp, "data": event.payload}
    return json.dumps(content, separators=(",", ":")).encode()


def deliver_due(
    *,
    limit: int = 100,
    transport: Transport | None = None,
    timeout: float = 10.0,
    schedule: Sequence[timedelta] = RETRY_SCHEDULE,
    disable_after: timedelta | None = timedelta(days=5),
    url_policy: URLPolicy | None = None,
    event_id: str | None = None,
    now: datetime | None = None,
    using: str | None = None,
) -> DeliveryReport:
    """Send up to ``limit`` due deliveries. Safe to run from several workers
    (each row uses a conditional claim and a token-checked finalization).

    :param limit: Deliveries handled in this call.
    :param transport: How requests are sent (default ``SafeHTTPTransport(url_policy)``).
    :param url_policy: Where the default transport may send requests.
    :param timeout: Seconds per request.
    :param schedule: Delays before each retry; the delivery fails after the last one.
    :param disable_after: Disable an endpoint failing continuously for this long.
    :param event_id: Only deliveries of this event.
    :param now: The current time (tests).
    :param using: Database containing the outbox.
    """
    if limit <= 0 or timeout <= 0 or any(delay.total_seconds() < 0 for delay in schedule):
        raise ValueError("limit and timeout must be positive; retry delays cannot be negative")
    send = transport or SafeHTTPTransport(url_policy or URLPolicy())
    report = DeliveryReport()
    database = using or router.db_for_write(WebhookDelivery)
    records = WebhookDelivery.objects.using(database)
    candidates = list(
        records.filter(
            status=WebhookDelivery.Status.PENDING,
            next_attempt_at__lte=now or timezone.now(),
            endpoint__is_active=True,
            **({"event_id": event_id} if event_id is not None else {}),
        )
        .order_by("next_attempt_at", "id")
        .values_list("pk", flat=True)[:limit]
    )
    for pk in candidates:
        # Claim each delivery immediately before sending, never the whole sequential
        # batch. A conditional UPDATE works on backends without SKIP LOCKED too.
        current = now or timezone.now()
        token = uuid.uuid4()
        claimed = records.filter(
            pk=pk,
            status=WebhookDelivery.Status.PENDING,
            next_attempt_at__lte=current,
            endpoint__is_active=True,
        ).update(lease_token=token, next_attempt_at=current + timedelta(seconds=timeout + 60))
        if not claimed:
            continue
        delivery = records.select_related("event", "endpoint").get(pk=pk)
        if delivery.lease_token != token:
            report.lost_claims += 1
            continue
        _attempt(delivery, send, timeout, schedule, disable_after, now, report, database)
    return report


def _attempt(
    delivery: WebhookDelivery,
    send: Transport,
    timeout: float,
    schedule: Sequence[timedelta],
    disable_after: timedelta | None,
    now: datetime | None,
    report: DeliveryReport,
    database: str,
) -> None:
    endpoint = delivery.endpoint
    body = body_of(delivery.event)
    headers = {
        "content-type": "application/json",
        "user-agent": "ninja-devx-webhooks",
        **signature_headers(
            endpoint.signing_secret,
            f"msg_{delivery.event.pk}",
            body,
            timestamp=int((now or timezone.now()).timestamp()),
        ),
    }
    status: int | None = None
    try:
        if not _audience_matches(delivery.event, endpoint):
            raise OSError("Endpoint no longer belongs to the event audience")
        code = send(endpoint.url, body, headers, timeout)
        status, error = code, "" if 200 <= code < 300 else f"HTTP {code}"
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"[:1000]
    completed = now or timezone.now()
    with transaction.atomic(using=database):
        # Every finalizer locks endpoint then delivery: disabling an endpoint updates
        # sibling deliveries and must not deadlock against another endpoint finalizer.
        endpoint = WebhookEndpoint.objects.using(database).select_for_update().get(pk=endpoint.pk)
        owned = (
            WebhookDelivery.objects.using(database)
            .select_for_update()
            .filter(
                pk=delivery.pk,
                lease_token=delivery.lease_token,
                status=WebhookDelivery.Status.PENDING,
            )
            .first()
        )
        if owned is None:
            report.lost_claims += 1
            return
        owned.attempts += 1
        owned.last_status_code = status
        owned.last_error = error
        owned.lease_token = None
        if not error:
            owned.status = WebhookDelivery.Status.SUCCEEDED
            owned.delivered_at = completed
            owned.next_attempt_at = None
            report.succeeded += 1
            if endpoint.failing_since is not None:
                endpoint.failing_since = None
                endpoint.save(using=database, update_fields=["failing_since"])
        elif owned.attempts > len(schedule) or _track_failure(
            endpoint, disable_after, completed, report
        ):
            owned.status = WebhookDelivery.Status.FAILED
            owned.next_attempt_at = None
            report.failed += 1
        else:
            owned.next_attempt_at = completed + schedule[owned.attempts - 1]
            report.retrying += 1
        owned.save(using=database)


def _audience_matches(event: OutboxEvent, endpoint: WebhookEndpoint) -> bool:
    raw: object = event.audience
    if not isinstance(raw, dict):
        return False
    audience = cast("dict[str, object]", raw)
    if audience.get("broadcast") is True:
        return True
    owner = audience.get("owner")
    return audience.get("tenant_key") == endpoint.tenant_key and (
        owner is None or owner == str(endpoint.owner_id)
    )


def _track_failure(
    endpoint: WebhookEndpoint,
    disable_after: timedelta | None,
    now: datetime,
    report: DeliveryReport,
) -> bool:
    """Remember when the endpoint started failing; disable it after ``disable_after``."""
    since: object = endpoint.failing_since
    if not isinstance(since, datetime):
        endpoint.failing_since = now
        endpoint.save(update_fields=["failing_since"])
        return False
    if disable_after is not None and endpoint.is_active and now - since >= disable_after:
        endpoint.is_active = False
        endpoint.disabled_reason = f"Failing since {since.isoformat()}"
        endpoint.save(update_fields=["is_active", "disabled_reason"])
        WebhookDelivery.objects.using(endpoint._state.db).filter(
            endpoint=endpoint, status=WebhookDelivery.Status.PENDING
        ).update(status=WebhookDelivery.Status.FAILED, next_attempt_at=None, lease_token=None)
        report.disabled_endpoints += 1
        return True
    return False
