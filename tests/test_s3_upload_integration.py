"""Real S3-compatible backend contract; uses a unique disposable bucket per test."""

import base64
import hashlib
import os
from uuid import uuid4

import httpx
import pytest
from django.contrib.auth.models import User
from ninja.testing import TestClient

from ninja_devx.contrib.uploads import S3Signer, UploadController, UploadPolicy

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not os.environ.get("TEST_S3_ENDPOINT_URL"), reason="requires an S3 test backend"
    ),
]


def test_real_presigned_post_checksum_version_and_replay():
    boto3 = pytest.importorskip("boto3")
    backend = boto3.client(
        "s3",
        endpoint_url=os.environ["TEST_S3_ENDPOINT_URL"],
        region_name="us-east-1",
        aws_access_key_id=os.environ["TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["TEST_S3_SECRET_KEY"],
    )
    bucket = f"cbv-upload-test-{uuid4().hex}"
    backend.create_bucket(Bucket=bucket)
    try:
        backend.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})

        class Uploads(UploadController):
            signer = S3Signer(bucket=bucket, client=backend)
            policy = UploadPolicy(
                content_types=("image/png",),
                max_bytes=100,
                require_checksum=True,
                require_version=True,
            )

        owner = User.objects.create(username="s3-owner")
        client = TestClient(Uploads.as_router())
        content = b"0123456789"
        checksum = base64.b64encode(hashlib.sha256(content).digest()).decode()
        signed = client.post(
            "/",
            json={
                "filename": "test.png",
                "content_type": "image/png",
                "size": len(content),
                "checksum_sha256": checksum,
            },
            user=owner,
        )
        assert signed.status_code == 201
        form = signed.json()
        uploaded = httpx.post(
            form["url"],
            data=form["fields"],
            files={"file": ("test.png", content, "image/png")},
            timeout=10,
        )
        assert uploaded.status_code in {200, 201, 204}, uploaded.text
        completed = client.post("/complete", json={"key": form["key"]}, user=owner)
        assert completed.status_code == 200, completed.content
        saved = completed.json()
        assert saved["checksum_sha256"] == checksum
        assert saved["version_id"]
        assert client.post("/complete", json={"key": form["key"]}, user=owner).json() == saved
        overwritten = httpx.post(
            form["url"],
            data=form["fields"],
            files={"file": ("test.png", content, "image/png")},
            timeout=10,
        )
        assert overwritten.status_code in {200, 201, 204}
        assert client.post("/complete", json={"key": form["key"]}, user=owner).status_code == 409
        pinned = backend.get_object(Bucket=bucket, Key=form["key"], VersionId=saved["version_id"])
        try:
            assert pinned["Body"].read() == content
        finally:
            pinned["Body"].close()
        rejected = httpx.post(
            form["url"],
            data=form["fields"],
            files={"file": ("test.png", b"wrong-data", "image/png")},
            timeout=10,
        )
        assert rejected.status_code in {400, 403}
    finally:
        # Only this test's generated bucket is enumerated or deleted.
        versions = backend.list_object_versions(Bucket=bucket)
        for item in [*versions.get("Versions", []), *versions.get("DeleteMarkers", [])]:
            backend.delete_object(Bucket=bucket, Key=item["Key"], VersionId=item["VersionId"])
        backend.delete_bucket(Bucket=bucket)
