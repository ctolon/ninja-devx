from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditEntry(models.Model):
    """One recorded action. ``changes`` maps field names to ``[old, new]``."""

    created: models.DateTimeField[object, object] = models.DateTimeField(
        auto_now_add=True, db_index=True
    )
    action: models.CharField[str, str] = models.CharField(max_length=32, db_index=True)
    actor: models.ForeignKey[models.Model | None, models.Model | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    actor_label: models.CharField[str, str] = models.CharField(max_length=150, blank=True)
    content_type: models.ForeignKey[ContentType | None, ContentType | None] = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    object_pk: models.CharField[str, str] = models.CharField(max_length=255, blank=True)
    object_repr: models.CharField[str, str] = models.CharField(max_length=200, blank=True)
    changes: models.JSONField[object, object] = models.JSONField(default=dict, blank=True)
    metadata: models.JSONField[object, object] = models.JSONField(default=dict, blank=True)
    request_id: models.CharField[str, str] = models.CharField(max_length=64, blank=True)
    method: models.CharField[str, str] = models.CharField(max_length=10, blank=True)
    path: models.CharField[str, str] = models.CharField(max_length=500, blank=True)
    ip_address: models.GenericIPAddressField[str | None, str | None] = models.GenericIPAddressField(
        null=True, blank=True
    )

    class Meta:
        verbose_name = "audit entry"
        verbose_name_plural = "audit entries"
        ordering = ("-created", "-id")
        indexes = [  # noqa: RUF012
            models.Index(fields=["content_type", "object_pk"], name="ndx_audit_object"),
            models.Index(fields=["actor", "created"], name="ndx_audit_actor"),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.object_repr or self.object_pk} by {self.actor_label}"
