"""Storage signing, inspection and deletion adapters."""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    fields: Mapping[str, str]
    key: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size: int
    content_type: str
    checksum_sha256: str | None = None
    version_id: str | None = None
    etag: str | None = None


class UploadSigner(Protocol):
    """Signs uploads for a storage backend and inspects what was stored."""

    def presign(
        self,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        expires_in: timedelta,
        checksum_sha256: str | None = None,
    ) -> PresignedUpload: ...

    def stat(self, key: str) -> StoredObject | None: ...

    def delete(self, key: str, *, version_id: str | None = None) -> None: ...


class S3Client(Protocol):
    def generate_presigned_post(
        self,
        Bucket: str,
        Key: str,
        Fields: Mapping[str, str] | None = None,
        Conditions: Sequence[object] | None = None,
        ExpiresIn: int = 3600,
    ) -> Mapping[str, object]: ...

    def head_object(self, Bucket: str, Key: str, ChecksumMode: str) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: str) -> Mapping[str, object]: ...


@dataclass(slots=True)
class S3Signer:
    """Presigned POST uploads to S3 (or MinIO, R2...) with boto3 (``ninja-devx[s3]``)."""

    bucket: str
    """Bucket name."""
    prefix: str = "uploads/"
    """Key prefix for every upload of this signer."""
    client: S3Client | None = None
    """A boto3 S3 client (default: ``boto3.client("s3")``, created on first use)."""

    def _client(self) -> S3Client:
        if self.client is None:
            boto3 = importlib.import_module("boto3")
            factory = cast("object", boto3.client)
            self.client = cast("S3Client", factory("s3"))  # type: ignore[operator]  # pyright: ignore[reportCallIssue]
        return self.client

    def presign(
        self,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        expires_in: timedelta,
        checksum_sha256: str | None = None,
    ) -> PresignedUpload:
        seconds = int(expires_in.total_seconds())
        fields = {"Content-Type": content_type}
        conditions: list[object] = [
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],
        ]
        if checksum_sha256 is not None:
            fields["x-amz-checksum-algorithm"] = "SHA256"
            fields["x-amz-checksum-sha256"] = checksum_sha256
            conditions.extend(
                [
                    {"x-amz-checksum-algorithm": "SHA256"},
                    {"x-amz-checksum-sha256": checksum_sha256},
                ]
            )
        signed = self._client().generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=seconds,
        )
        signed_fields = cast("Mapping[str, object]", signed["fields"])
        return PresignedUpload(
            url=str(signed["url"]),
            fields={name: str(value) for name, value in signed_fields.items()},
            key=key,
            expires_at=datetime.now(UTC) + expires_in,
        )

    def stat(self, key: str) -> StoredObject | None:
        try:
            head = self._client().head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except Exception as exc:  # botocore's ClientError, without importing botocore
            response: object = getattr(exc, "response", None)
            error = cast("Mapping[str, Mapping[str, object]]", response or {}).get("Error", {})
            if str(error.get("Code")) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        size: object = head.get("ContentLength", 0)
        return StoredObject(
            key=key,
            size=size if isinstance(size, int) else 0,
            content_type=str(head.get("ContentType", "")),
            checksum_sha256=str(head["ChecksumSHA256"]) if head.get("ChecksumSHA256") else None,
            version_id=str(head["VersionId"])
            if head.get("VersionId") not in (None, "null")
            else None,
            etag=str(head["ETag"]) if head.get("ETag") else None,
        )

    def delete(self, key: str, *, version_id: str | None = None) -> None:
        options = {"Bucket": self.bucket, "Key": key}
        if version_id is not None:
            options["VersionId"] = version_id
        self._client().delete_object(**options)


@dataclass(slots=True)
class FakeSigner:
    """In-memory signer for tests: ``signer.store(key, size, content_type)`` simulates the
    client's upload."""

    url: str = "https://storage.test/upload"
    objects: dict[str, StoredObject] = field(default_factory=dict[str, StoredObject])
    signed: list[str] = field(default_factory=list[str])

    def presign(
        self,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        expires_in: timedelta,
        checksum_sha256: str | None = None,
    ) -> PresignedUpload:
        self.signed.append(key)
        fields = {"key": key, "Content-Type": content_type, "max-bytes": str(max_bytes)}
        if checksum_sha256 is not None:
            fields["x-amz-checksum-sha256"] = checksum_sha256
        return PresignedUpload(
            url=self.url,
            fields=fields,
            key=key,
            expires_at=datetime.now(UTC) + expires_in,
        )

    def stat(self, key: str) -> StoredObject | None:
        return self.objects.get(key)

    def store(
        self,
        key: str,
        size: int,
        content_type: str,
        *,
        checksum_sha256: str | None = None,
        version_id: str | None = None,
    ) -> None:
        self.objects[key] = StoredObject(
            key=key,
            size=size,
            content_type=content_type,
            checksum_sha256=checksum_sha256,
            version_id=version_id or uuid.uuid4().hex,
        )

    def delete(self, key: str, *, version_id: str | None = None) -> None:
        current = self.objects.get(key)
        if current is not None and (version_id is None or current.version_id == version_id):
            del self.objects[key]
