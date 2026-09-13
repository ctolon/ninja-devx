from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models


def validate_rate(value: str) -> None:
    from ...http.throttling import parse_rate

    try:
        parse_rate(value)
    except ImproperlyConfigured as exc:
        raise ValidationError(str(exc)) from exc


class APIKey(models.Model):
    """A key belonging to a user, limited to ``scopes`` (``["orders:read", "orders:*"]``)."""

    name: models.CharField[str, str] = models.CharField(max_length=100)
    prefix: models.CharField[str, str] = models.CharField(max_length=16, unique=True)
    hashed_secret: models.CharField[str, str] = models.CharField(max_length=64)
    user: models.ForeignKey[models.Model, models.Model] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    scopes: models.JSONField[object, object] = models.JSONField(default=list, blank=True)
    rate_limit: models.CharField[str, str] = models.CharField(
        max_length=32,
        blank=True,
        validators=[validate_rate],
        help_text='Requests allowed for this key with APIKeyRateThrottle, e.g. "1000/hour".',
    )
    created: models.DateTimeField[object, object] = models.DateTimeField(auto_now_add=True)
    last_used_at: models.DateTimeField[object, object] = models.DateTimeField(null=True, blank=True)
    expires_at: models.DateTimeField[object, object] = models.DateTimeField(null=True, blank=True)
    revoked_at: models.DateTimeField[object, object] = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "API key"
        ordering = ("-created",)

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix})"
