import base64
import hashlib
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from ninja.testing import TestClient

from ninja_devx.contrib.uploads import FakeSigner, UploadController, UploadPolicy
from ninja_devx.contrib.uploads.models import UploadRecord

pytestmark = pytest.mark.django_db


@pytest.fixture
def uploads():
    backend = FakeSigner()

    class Uploads(UploadController):
        signer = backend
        policy = UploadPolicy(content_types=("image/png",), max_bytes=100)

    return Uploads, backend, TestClient(Uploads.as_router()), User.objects.create(username="owner")


def sign(client, user, **changes):
    payload = {"filename": "image.png", "size": 10, "content_type": "image/png", **changes}
    response = client.post("/", json=payload, user=user)
    assert response.status_code == 201
    return response.json()["key"]


def test_only_issued_keys_can_complete_and_exact_size_is_checked(uploads):
    _cls, backend, client, owner = uploads
    forged = f"{owner.pk}/forged/image.png"
    backend.store(forged, 10, "image/png")
    assert client.post("/complete", json={"key": forged}, user=owner).status_code == 404
    key = sign(client, owner)
    backend.store(key, 9, "image/png")
    assert client.post("/complete", json={"key": key}, user=owner).status_code == 422
    assert UploadRecord.objects.get(key=key).state == "pending"


def test_checksum_and_completed_version_cannot_change(uploads):
    _cls, backend, client, owner = uploads
    digest = base64.b64encode(hashlib.sha256(b"0123456789").digest()).decode()
    key = sign(client, owner, checksum_sha256=digest)
    backend.store(key, 10, "image/png", checksum_sha256="wrong")
    assert client.post("/complete", json={"key": key}, user=owner).status_code == 422
    backend.store(key, 10, "image/png", checksum_sha256=digest, version_id="version-one")
    response = client.post("/complete", json={"key": key}, user=owner)
    assert response.status_code == 200
    assert response.json()["version_id"] == "version-one"
    assert UploadRecord.objects.get(key=key).state == "complete"
    assert client.post("/complete", json={"key": key}, user=owner).json() == response.json()
    backend.store(key, 10, "image/png", checksum_sha256=digest, version_id="version-two")
    assert client.post("/complete", json={"key": key}, user=owner).status_code == 409


def test_expiry_cleanup_is_bounded_preserves_completed_and_live_uploads(uploads):
    cls, backend, client, owner = uploads
    keys = [sign(client, owner) for _ in range(4)]
    for key in keys:
        backend.store(key, 10, "image/png")
    assert client.post("/complete", json={"key": keys[0]}, user=owner).status_code == 200
    past = timezone.now() - timedelta(hours=1)
    UploadRecord.objects.filter(key__in=keys[:3]).update(expires_at=past)
    assert client.post("/complete", json={"key": keys[1]}, user=owner).status_code == 410
    assert cls().cleanup_expired(limit=1) == 1
    assert UploadRecord.objects.filter(state="expired").count() == 1
    assert cls().cleanup_expired(limit=10) == 1
    assert backend.stat(keys[0]) is not None
    assert backend.stat(keys[3]) is not None
    assert backend.stat(keys[1]) is None
    assert backend.stat(keys[2]) is None
    assert cls().cleanup_expired() == 0


def test_checksum_requirement_and_policy_validation(uploads):
    cls, _backend, client, owner = uploads
    cls.policy = UploadPolicy(require_checksum=True)
    response = client.post(
        "/", json={"filename": "x.png", "size": 1, "content_type": "image/png"}, user=owner
    )
    assert response.status_code == 422
    assert UploadRecord.objects.count() == 0
    with pytest.raises(ValueError, match="positive"):
        UploadPolicy(max_bytes=0)
    with pytest.raises(ValueError, match="positive"):
        UploadPolicy(expires_in=timedelta(0))


def test_cleanup_failure_remains_retryable_and_command_is_bounded(uploads, monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    from ninja_devx.management.commands import devx_uploads

    cls, backend, client, owner = uploads
    key = sign(client, owner)
    backend.store(key, 10, "image/png")
    UploadRecord.objects.filter(key=key).update(expires_at=timezone.now() - timedelta(hours=1))

    def unavailable(self, key, *, version_id=None):
        raise OSError("storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(FakeSigner, "delete", unavailable)
        with pytest.raises(OSError, match="storage unavailable"):
            cls().cleanup_expired()
    assert UploadRecord.objects.get(key=key).state == "pending"
    monkeypatch.setattr(devx_uploads, "import_string", lambda name: cls)
    output = StringIO()
    call_command("devx_uploads", "app.Uploads", limit=1, stdout=output)
    assert output.getvalue().strip() == '{"cleaned": 1}'
    assert backend.stat(key) is None
