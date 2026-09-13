"""Run only in a fresh Django process with custom_userapp.settings."""

import pytest
from django.contrib.auth import get_user_model
from ninja import Schema
from ninja.testing import TestClient

from ninja_devx.contrib.apikeys.api import APIKeyController
from ninja_devx.contrib.audit.api import AuditLogController
from ninja_devx.contrib.audit.log import record
from ninja_devx.crud import CRUDController, ObjectSharingMixin
from ninja_devx.security.object_permissions import ObjectPermissions, assign_perm
from tests.testapp.models import Note


@pytest.mark.django_db
def test_uuid_users_can_share_revoke_audit_and_manage_keys(rf):
    user_model = get_user_model()
    owner = user_model.objects.create(username="owner", is_staff=True)
    reader = user_model.objects.create(username="reader")
    note = Note.objects.create(owner=owner, text="shared")
    for action in ("view", "change"):
        assign_perm(f"testapp.{action}_note", owner, note)

    class NoteOut(Schema):
        id: int
        text: str

    class NoteIn(Schema):
        text: str

    class Notes(ObjectSharingMixin[Note], CRUDController[Note, NoteOut, NoteIn]):
        object_permissions = ObjectPermissions()

    client = TestClient(Notes.as_router())
    shared = client.put(
        f"/{note.pk}/permissions",
        json={"user_id": str(reader.pk), "permissions": ["view"]},
        user=owner,
    )
    assert shared.status_code == 200
    assert str(reader.pk) in {item["user_id"] for item in shared.json()}
    assert client.get(f"/{note.pk}", user=reader).status_code == 200
    revoked = client.post(
        f"/{note.pk}/permissions/revoke", json={"user_id": str(reader.pk)}, user=owner
    )
    assert revoked.status_code == 200
    assert client.get(f"/{note.pk}", user=reader).status_code == 404
    invalid = client.put(
        f"/{note.pk}/permissions",
        json={"user_id": "not-a-uuid", "permissions": ["view"]},
        user=owner,
    )
    assert invalid.status_code == 422

    request = rf.post("/")
    request.user = owner
    entry = record(request, "create", note)
    response = TestClient(AuditLogController.as_router()).get(f"/{entry.pk}", user=owner)
    assert response.status_code == 200
    assert response.json()["actor_id"] == str(owner.pk)

    keys = TestClient(APIKeyController.as_router())
    key = keys.post("/", json={"name": "ci"}, user=owner)
    assert key.status_code == 201
    assert keys.delete(f"/{key.json()['id']}", user=owner).status_code == 204
