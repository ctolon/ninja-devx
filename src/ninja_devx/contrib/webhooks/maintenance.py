"""Bounded retention and retry operations for workers, APIs and administrators."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from .models import OutboxEvent, WebhookDelivery


def retry_deliveries(deliveries: QuerySet[WebhookDelivery]) -> int:
    """Reset unclaimed rows; a running worker's ownership is never revoked."""
    return deliveries.filter(lease_token__isnull=True).update(
        status=WebhookDelivery.Status.PENDING,
        next_attempt_at=timezone.now(),
        attempts=0,
        last_error="",
        last_status_code=None,
        delivered_at=None,
    )


def prune_events(*, days: int = 30, limit: int = 1000, using: str = "default") -> int:
    """Delete at most limit old events whose deliveries are all terminal.

    Lock deliveries before testing their state, so a concurrent retry cannot be
    deleted after becoming pending. Events with even one pending delivery are retained.
    """
    if days < 1 or limit < 1:
        raise ValueError("days and limit must be positive")
    events = OutboxEvent.objects.using(using)
    candidates = list(
        events.filter(created__lt=timezone.now() - timedelta(days=days)).values_list(
            "pk", flat=True
        )[:limit]
    )
    removed = 0
    for pk in candidates:
        with transaction.atomic(using=using):
            event = events.select_for_update().filter(pk=pk).first()
            if event is None:
                continue
            states = (
                WebhookDelivery.objects.using(using)
                .select_for_update()
                .filter(event_id=pk)
                .values_list("status", flat=True)
                .iterator()
            )
            if any(status == WebhookDelivery.Status.PENDING for status in states):
                continue
            event.delete(using=using)
            removed += 1
    return removed
