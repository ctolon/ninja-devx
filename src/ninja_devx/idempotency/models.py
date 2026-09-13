"""Durable request ownership. Unfinished claims never expire automatically."""

from __future__ import annotations

import uuid
from datetime import datetime

from django.db import models


class IdempotencyRecord(models.Model):
    key: models.CharField[str, str] = models.CharField(primary_key=True, max_length=64)
    token: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        default=uuid.uuid4, editable=False
    )
    fingerprint: models.CharField[str, str] = models.CharField(max_length=64)
    state: models.CharField[str, str] = models.CharField(max_length=12, default="running")
    status: models.PositiveSmallIntegerField[int | None, int | None] = (
        models.PositiveSmallIntegerField(null=True)
    )
    content: models.BinaryField[bytes, bytes] = models.BinaryField(default=bytes)
    headers: models.JSONField[object, object] = models.JSONField(default=list)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)
    expires_at: models.DateTimeField[datetime | None, datetime | None] = models.DateTimeField(
        null=True, db_index=True
    )

    class Meta:
        verbose_name = "idempotency record"
