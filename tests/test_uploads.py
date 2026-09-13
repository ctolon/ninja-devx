from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from ninja.testing import TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.contrib.uploads import (
    FakeSigner,
    S3Signer,
    UploadController,
    UploadPolicy,
    safe_filename,
)

pytestmark = pytest.mark.django_db

signer = FakeSigner()


class Avatars(UploadController):
    signer = signer
    policy = UploadPolicy(content_types=("image/*",), max_bytes=1000)
    key_prefix = "avatars/"


@pytest.fixture
def ada():
    return User.objects.create(username="ada")


def test_sign_then_complete(ada):
    client = TestClient(Avatars.as_router())
    signed = client.post(
        "/", json={"filename": "../Me é.png", "content_type": "image/png", "size": 10}, user=ada
    )
    assert signed.status_code == 201
    body = signed.json()
    assert body["key"].startswith(f"avatars/{ada.pk}/")
    assert body["key"].endswith("/Me_e.png")
    assert body["fields"]["Content-Type"] == "image/png"

    assert client.post("/complete", json={"key": body["key"]}, user=ada).status_code == 404
    signer.store(body["key"], 10, "image/png")
    done = client.post("/complete", json={"key": body["key"]}, user=ada)
    assert done.json() == {
        "key": body["key"],
        "size": 10,
        "content_type": "image/png",
        "checksum_sha256": None,
        "version_id": signer.stat(body["key"]).version_id,
        "etag": None,
    }


def test_policy_and_ownership(ada):
    bob = User.objects.create(username="bob")
    client = TestClient(Avatars.as_router())
    assert (
        client.post(
            "/", json={"filename": "a.pdf", "content_type": "application/pdf", "size": 1}, user=ada
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/", json={"filename": "a.png", "content_type": "image/png", "size": 5000}, user=ada
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/", json={"filename": "a.png", "content_type": "image/png", "size": 1}
        ).status_code
        == 401
    )

    key = client.post(
        "/", json={"filename": "a.png", "content_type": "image/png", "size": 10}, user=ada
    ).json()["key"]
    signer.store(key, 10, "image/png")
    assert client.post("/complete", json={"key": key}, user=bob).status_code == 404
    signer.store(key, 10, "text/html")  # the client lied about the type
    assert client.post("/complete", json={"key": key}, user=ada).status_code == 422


def test_signer_is_required():
    class Missing(UploadController):
        pass

    with pytest.raises(ControllerConfigError, match="signer"):
        Missing.as_router()


def test_safe_filename():
    assert safe_filename("C:\\temp\\..\\évil name.tar.gz") == "evil_name.tar.gz"
    assert safe_filename("...") == "file"
    assert len(safe_filename("a" * 300 + ".txt")) == 100


class StubS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_post(self, Bucket, Key, Fields=None, Conditions=None, ExpiresIn=3600):
        self.calls.append((Bucket, Key, Fields, Conditions, ExpiresIn))
        return {"url": "https://bucket.s3.amazonaws.com/", "fields": {"key": Key, "policy": "p"}}

    def head_object(self, Bucket, Key, ChecksumMode="ENABLED"):
        if Key == "missing":
            error = Exception("not found")
            error.response = {"Error": {"Code": "404"}}
            raise error
        return {"ContentLength": 42, "ContentType": "image/png"}


def test_s3_signer_with_a_client_stub():
    client = StubS3()
    s3 = S3Signer(bucket="media", client=client)
    signed = s3.presign(
        "uploads/1/a.png", content_type="image/png", max_bytes=99, expires_in=timedelta(minutes=5)
    )
    assert signed.fields == {"key": "uploads/1/a.png", "policy": "p"}
    assert client.calls[0] == (
        "media",
        "uploads/1/a.png",
        {"Content-Type": "image/png"},
        [{"Content-Type": "image/png"}, ["content-length-range", 1, 99]],
        300,
    )
    assert s3.stat("uploads/1/a.png").size == 42
    assert s3.stat("missing") is None
