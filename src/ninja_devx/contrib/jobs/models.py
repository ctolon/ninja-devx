"""Persistence for background jobs started from a request and finished by a worker."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Job(models.Model):
    """One run of a function started with ``start_job`` and executed by ``run_job``."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    id: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    name: models.CharField[str, str] = models.CharField(max_length=200)
    status: models.CharField[str, str] = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    function: models.CharField[str, str] = models.CharField(
        max_length=255,
        help_text="Name registered with @job, or a dotted import path; resolved by run_job.",
    )
    arguments: models.JSONField[object, object] = models.JSONField(
        default=dict, blank=True, encoder=DjangoJSONEncoder
    )
    created_by: models.ForeignKey[models.Model | None, models.Model | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="devx_jobs",
    )
    tenant: models.CharField[str, str] = models.CharField(max_length=200, blank=True, db_index=True)
    progress: models.PositiveSmallIntegerField[int, int] = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    result: models.JSONField[object, object] = models.JSONField(
        default=dict, blank=True, encoder=DjangoJSONEncoder
    )
    error: models.TextField[str, str] = models.TextField(blank=True)
    attempts: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    created: models.DateTimeField[object, object] = models.DateTimeField(auto_now_add=True)
    started: models.DateTimeField[object, object] = models.DateTimeField(null=True, blank=True)
    finished: models.DateTimeField[object, object] = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created",)
        indexes = [  # noqa: RUF012
            models.Index(fields=["created_by", "-created"], name="ndx_job_owner_created"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"
