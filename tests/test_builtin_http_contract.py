"""Wire-level status, media and header matrix for composed built-in CRUD operations."""

import pytest
from ninja.testing import TestClient

from tests.test_conditional import StrictNotes
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db


def test_conditional_crud_status_media_and_headers(django_user_model):
    owner = django_user_model.objects.create(username="ada")
    client = TestClient(StrictNotes.as_router())
    created = client.post("/", json={"text": "first"})
    assert created.status_code == 201
    assert created["Content-Type"].startswith("application/json")
    pk = created.json()["id"]
    fetched = client.get(f"/{pk}")
    assert fetched.status_code == 200
    assert fetched["Content-Type"].startswith("application/json")
    unchanged = client.get(f"/{pk}", headers={"If-None-Match": fetched["ETag"]})
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged["ETag"] == fetched["ETag"]
    for headers, status in [({}, 428), ({"If-Match": '"stale"'}, 412)]:
        denied = client.patch(f"/{pk}", json={"text": "changed"}, headers=headers)
        assert denied.status_code == status
        assert denied["Content-Type"].startswith("application/json")
        assert denied.json()["code"] in ("precondition_required", "precondition_failed")
    invalid = client.put(f"/{pk}", json={}, headers={"If-Match": fetched["ETag"]})
    assert invalid.status_code == 422
    assert invalid["Content-Type"].startswith("application/json")
    deleted = client.delete(f"/{pk}", headers={"If-Match": fetched["ETag"]})
    assert deleted.status_code == 204
    assert deleted.content == b""
    missing = client.get(f"/{pk}")
    assert missing.status_code == 404
    assert missing["Content-Type"].startswith("application/json")
    assert not Note.objects.filter(owner=owner).exists()
