"""Django model discovery for optional core persistence features, and abstract model bases."""

from .contrib.uploads.models import UploadRecord
from .idempotency.models import IdempotencyRecord
from .stamps import SoftDeletable, Stamped, TimeStamped, UserStamped

__all__ = [
    "IdempotencyRecord",
    "SoftDeletable",
    "Stamped",
    "TimeStamped",
    "UploadRecord",
    "UserStamped",
]
