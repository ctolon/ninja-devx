"""Django model discovery for optional core persistence features."""

from .contrib.uploads.models import UploadRecord
from .idempotency.models import IdempotencyRecord

__all__ = ["IdempotencyRecord", "UploadRecord"]
