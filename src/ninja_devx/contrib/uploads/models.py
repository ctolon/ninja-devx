"""Core persistence for upload authorization and completion metadata."""

from __future__ import annotations

from datetime import datetime

from django.db import models


class UploadRecord(models.Model):
    key_digest: models.CharField[str, str] = models.CharField(max_length=64, unique=True)
    key: models.TextField[str, str] = models.TextField()
    namespace: models.CharField[str, str] = models.CharField(max_length=64, db_index=True)
    owner_scope: models.CharField[str, str] = models.CharField(max_length=64)
    expected_size: models.PositiveBigIntegerField[int, int] = models.PositiveBigIntegerField()
    content_type: models.CharField[str, str] = models.CharField(max_length=255)
    checksum_sha256: models.CharField[str, str] = models.CharField(max_length=44, blank=True)
    version_id: models.TextField[str, str] = models.TextField(blank=True)
    etag: models.TextField[str, str] = models.TextField(blank=True)
    state: models.CharField[str, str] = models.CharField(max_length=16, default="pending")
    created: models.DateTimeField[object, object] = models.DateTimeField(auto_now_add=True)
    expires_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(db_index=True)
    completed_at: models.DateTimeField[object, object] = models.DateTimeField(null=True)
    cleaned_at: models.DateTimeField[object, object] = models.DateTimeField(null=True)

    class Meta:
        app_label = "ninja_devx"
