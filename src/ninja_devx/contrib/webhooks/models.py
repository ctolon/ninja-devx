from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class WebhookEndpoint(models.Model):
    """A URL receiving the events it subscribes to (``["order.*"]``, ``["*"]``)."""

    owner_id: int | str | uuid.UUID | None

    url: models.URLField[str, str] = models.URLField(max_length=500)
    description: models.CharField[str, str] = models.CharField(max_length=200, blank=True)
    events: models.JSONField[object, object] = models.JSONField(default=list)
    secret: models.TextField[str, str] = models.TextField(
        help_text="Signing secret; encrypted when WEBHOOK_SECRET_KEYS is set."
    )
    owner: models.ForeignKey[models.Model | None, models.Model | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
    )
    tenant_key: models.CharField[str, str] = models.CharField(
        max_length=200, blank=True, db_index=True
    )
    is_active: models.BooleanField[bool, bool] = models.BooleanField(default=True)
    failing_since: models.DateTimeField[object, object] = models.DateTimeField(
        null=True, blank=True
    )
    disabled_reason: models.CharField[str, str] = models.CharField(max_length=200, blank=True)
    created: models.DateTimeField[object, object] = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        return self.url

    @property
    def signing_secret(self) -> str:
        """The raw secret used to sign requests (decrypted when needed)."""
        from .secrets import decrypt_secret

        return decrypt_secret(self.secret)

    def set_secret(self, raw: str) -> None:
        """Store ``raw`` (encrypted when ``WEBHOOK_SECRET_KEYS`` is set); call ``save()``."""
        from .secrets import encrypt_secret

        self.secret = encrypt_secret(raw)


class OutboxEvent(models.Model):
    """An event committed with the change that caused it."""

    id: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    event_type: models.CharField[str, str] = models.CharField(max_length=100, db_index=True)
    payload: models.JSONField[object, object] = models.JSONField(default=dict)
    audience: models.JSONField[object, object] = models.JSONField(default=dict)
    created: models.DateTimeField[object, object] = models.DateTimeField(
        auto_now_add=True, db_index=True
    )

    class Meta:
        ordering = ("created",)

    def __str__(self) -> str:
        return f"{self.event_type} {self.id}"


class WebhookDelivery(models.Model):
    """One event sent to one endpoint, with its retry state."""

    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        """Gave up after the last retry, or the endpoint was disabled."""

    event: models.ForeignKey[OutboxEvent, OutboxEvent] = models.ForeignKey(
        OutboxEvent, on_delete=models.CASCADE, related_name="deliveries"
    )
    endpoint: models.ForeignKey[WebhookEndpoint, WebhookEndpoint] = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    status: models.CharField[str, str] = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    attempts: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    lease_token: models.UUIDField[uuid.UUID | None, uuid.UUID | None] = models.UUIDField(
        null=True, editable=False
    )
    next_attempt_at: models.DateTimeField[object, object] = models.DateTimeField(
        null=True, blank=True
    )
    last_status_code: models.PositiveIntegerField[int | None, int | None] = (
        models.PositiveIntegerField(null=True, blank=True)
    )
    last_error: models.TextField[str, str] = models.TextField(blank=True)
    delivered_at: models.DateTimeField[object, object] = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "webhook deliveries"
        ordering = ("id",)
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(fields=["event", "endpoint"], name="ndx_webhook_once"),
        ]
        indexes = [  # noqa: RUF012
            models.Index(fields=["status", "next_attempt_at"], name="ndx_webhook_due"),
        ]

    def __str__(self) -> str:
        return f"delivery {self.pk} ({self.status})"
