"""Direct-to-storage uploads with presigned requests (S3 and compatible stores).

Clients ask the API for a short-lived signed form, upload the bytes straight to the
bucket (the API never streams them), then confirm; the confirmation checks what was
actually stored::

    class AvatarUploads(UploadController):
        signer = S3Signer(bucket="media", prefix="avatars/")
        policy = UploadPolicy(content_types=("image/png", "image/jpeg"), max_bytes=2_000_000)

    api.add_router("/uploads/avatars", AvatarUploads.as_router())

- ``POST /``: ``{"filename", "content_type", "size"}`` → ``{"url", "fields", "key", ...}``;
  the client sends a ``multipart/form-data`` POST with ``fields`` plus ``file`` to ``url``.
  The store enforces the size range and content type.
- ``POST /complete``: ``{"key"}`` → the stored object's size and type, after checking the
  key belongs to the caller and still matches the policy.

Keys are ``<prefix><user id>/<uuid>/<sanitized filename>``. Use ``FakeSigner`` in tests.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar
from urllib.parse import quote

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja import Schema, Status
from ninja.errors import HttpError
from pydantic import Field

from ...exceptions import ControllerConfigError
from ...routing.controller import Controller, ControllerOptions
from ...routing.operations import OperationSpec, post
from ...security.auth import request_user
from ...security.permissions import IsAuthenticated
from .backends import FakeSigner, PresignedUpload, S3Signer, StoredObject, UploadSigner
from .models import UploadRecord

__all__ = [
    "FakeSigner",
    "PresignedUpload",
    "S3Signer",
    "StoredObject",
    "UploadController",
    "UploadPolicy",
    "UploadSigner",
    "safe_filename",
]


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    """What may be uploaded."""

    content_types: Sequence[str] = ("*/*",)
    """Allowed media types; ``"image/*"`` allows a family."""
    max_bytes: int = 10 * 1024 * 1024
    """Largest accepted size."""
    expires_in: timedelta = timedelta(minutes=10)
    """How long the signed form stays valid."""

    cleanup_grace: timedelta = timedelta(minutes=15)
    """Grace after form expiry, allowing in-flight uploads to finish before cleanup."""
    require_checksum: bool = False
    """Require a base64 SHA-256 digest in the signed request and stored object."""
    require_version: bool = False
    """Require storage versioning; consumers must read the returned version_id."""

    def __post_init__(self) -> None:
        if (
            self.max_bytes <= 0
            or self.expires_in.total_seconds() <= 0
            or self.cleanup_grace.total_seconds() < 0
        ):
            raise ValueError("Upload size and expiry must be positive")

    def allows_type(self, content_type: str) -> bool:
        main = content_type.split(";", 1)[0].strip().lower()
        family = main.split("/", 1)[0]
        return any(allowed in {main, "*/*", f"{family}/*"} for allowed in self.content_types)


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, *, max_length: int = 100) -> str:
    """ASCII, no directories, no leading dots: ``"../Résumé 2024.pdf"`` → ``"Resume_2024.pdf"``.

    :param name: The client's file name.
    :param max_length: Longest result, extension included.
    """
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    base = posixpath.basename(name.replace("\\", "/"))
    ascii_name = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    cleaned = _UNSAFE.sub("_", ascii_name).strip("._") or "file"
    stem, extension = posixpath.splitext(cleaned)
    if len(extension) >= max_length:
        return cleaned[:max_length]
    return stem[: max_length - len(extension)] + extension


class UploadIn(Schema):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=255)
    size: int = Field(gt=0, description="Bytes the client will upload.")
    checksum_sha256: str | None = Field(None, pattern=r"^[A-Za-z0-9+/]{43}=$")


class UploadOut(Schema):
    url: str
    fields: dict[str, str] = Field(description="Form fields to send before `file`.")
    key: str
    expires_at: datetime


class CompleteIn(Schema):
    key: str = Field(min_length=1, max_length=1024)


class StoredOut(Schema):
    key: str
    size: int
    content_type: str
    checksum_sha256: str | None = None
    version_id: str | None = None
    etag: str | None = None


class UploadController(Controller):
    """``POST /`` signs an upload, ``POST /complete`` verifies it. Set ``signer``."""

    options = ControllerOptions(permissions=[IsAuthenticated()], tags=["uploads"])
    signer: ClassVar[UploadSigner | None] = None
    """Storage backend (``S3Signer``, ``FakeSigner`` or your own)."""
    policy: ClassVar[UploadPolicy] = UploadPolicy()
    """Allowed types, size and expiry."""
    upload_database: ClassVar[str] = "default"
    """Alias containing core UploadRecord rows (run migrations on it)."""
    key_prefix: ClassVar[str] = ""
    """Added before ``<user id>/<uuid>/<filename>`` (after the signer's own prefix)."""

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        if cls.signer is None:
            raise ControllerConfigError(f"{cls.__qualname__}.signer is not set")
        return super().customize_operation(name, spec)

    def get_signer(self) -> UploadSigner:
        signer = type(self).signer
        if signer is None:
            raise ControllerConfigError(f"{type(self).__qualname__}.signer is not set")
        return signer

    def owner_prefix(self, request: HttpRequest) -> str:
        """The part of the key reserved to the caller (override for tenants)."""
        user = request_user(request)
        if user is None:
            raise HttpError(401, _("Authentication credentials were not provided."))
        user_id: object = getattr(user, "pk", None)
        return f"{self._signer_prefix()}{type(self).key_prefix}{quote(str(user_id), safe='')}/"

    def _signer_prefix(self) -> str:
        prefix: object = getattr(self.get_signer(), "prefix", "")
        return prefix if isinstance(prefix, str) else ""

    @post("/", response={201: UploadOut}, summary="Sign an upload")
    def sign_upload(self, request: HttpRequest, payload: UploadIn) -> Status[PresignedUpload]:
        policy = type(self).policy
        if not policy.allows_type(payload.content_type):
            raise HttpError(
                415,
                _("Content type %(type)s is not allowed") % {"type": payload.content_type},
            )
        if payload.size > policy.max_bytes:
            raise HttpError(
                413, _("Files are limited to %(bytes)d bytes") % {"bytes": policy.max_bytes}
            )
        if policy.require_checksum and payload.checksum_sha256 is None:
            raise HttpError(422, _("A SHA-256 checksum is required"))
        key = f"{self.owner_prefix(request)}{uuid.uuid4().hex}/{safe_filename(payload.filename)}"
        signed = self.get_signer().presign(
            key,
            content_type=payload.content_type,
            max_bytes=payload.size,
            expires_in=policy.expires_in,
            checksum_sha256=payload.checksum_sha256,
        )
        UploadRecord.objects.using(type(self).upload_database).create(
            key_digest=_digest(key),
            key=key,
            namespace=self.upload_namespace(),
            owner_scope=_digest(self.owner_prefix(request)),
            expected_size=payload.size,
            content_type=payload.content_type,
            checksum_sha256=payload.checksum_sha256 or "",
            expires_at=signed.expires_at,
        )
        return Status(201, signed)

    def upload_namespace(self) -> str:
        signer = self.get_signer()
        return _digest(
            json.dumps(
                [
                    type(self).__module__,
                    type(self).__qualname__,
                    type(self).key_prefix,
                    self._signer_prefix(),
                    str(getattr(signer, "bucket", "")),
                ]
            )
        )

    @post("/complete", response=StoredOut, summary="Confirm an upload")
    def complete_upload(self, request: HttpRequest, payload: CompleteIn) -> StoredObject:
        with transaction.atomic(using=type(self).upload_database):
            record = (
                UploadRecord.objects.using(type(self).upload_database)
                .select_for_update()
                .filter(
                    key_digest=_digest(payload.key),
                    namespace=self.upload_namespace(),
                    owner_scope=_digest(self.owner_prefix(request)),
                )
                .first()
            )
            if record is None or record.key != payload.key:
                raise HttpError(404, _("Upload not found"))
            if record.state == "expired" or (
                record.state == "pending" and record.expires_at <= timezone.now()
            ):
                raise HttpError(410, _("Upload authorization has expired"))
            stored = self.get_signer().stat(payload.key)
            if stored is None:
                raise HttpError(404, _("Upload not found"))
            policy = type(self).policy
            if (
                stored.size != record.expected_size
                or stored.size > policy.max_bytes
                or stored.content_type != record.content_type
                or not policy.allows_type(stored.content_type)
                or (record.checksum_sha256 and stored.checksum_sha256 != record.checksum_sha256)
                or (policy.require_checksum and not stored.checksum_sha256)
                or (policy.require_version and not stored.version_id)
            ):
                raise HttpError(422, _("The stored file does not match the upload policy"))
            if record.state == "complete":
                if record.version_id != (stored.version_id or "") or record.etag != (
                    stored.etag or ""
                ):
                    raise HttpError(409, _("The completed upload has changed"))
                return stored
            record.state = "complete"
            record.version_id = stored.version_id or ""
            record.etag = stored.etag or ""
            record.checksum_sha256 = stored.checksum_sha256 or ""
            record.completed_at = timezone.now()
            record.save(
                using=type(self).upload_database,
                update_fields=[
                    "state",
                    "version_id",
                    "etag",
                    "checksum_sha256",
                    "completed_at",
                ],
            )
            return stored

    def cleanup_expired(self, *, limit: int = 100) -> int:
        """Delete current objects from expired pending uploads; completed records are retained."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        alias = type(self).upload_database
        cutoff = timezone.now() - type(self).policy.cleanup_grace
        candidates = list(
            UploadRecord.objects.using(alias)
            .filter(
                namespace=self.upload_namespace(),
                state="pending",
                expires_at__lt=cutoff,
            )
            .order_by("expires_at", "pk")
            .values_list("pk", flat=True)[:limit]
        )
        cleaned = 0
        for pk in candidates:
            with transaction.atomic(using=alias):
                record = UploadRecord.objects.using(alias).select_for_update().filter(pk=pk).first()
                if record is None or record.state != "pending" or record.expires_at >= cutoff:
                    continue
                stored = self.get_signer().stat(record.key)
                if stored is not None:
                    self.get_signer().delete(record.key, version_id=stored.version_id)
                record.state = "expired"
                record.cleaned_at = timezone.now()
                record.save(using=alias, update_fields=["state", "cleaned_at"])
                cleaned += 1
        return cleaned


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
