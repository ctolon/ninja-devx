import pytest
from django.contrib.auth.models import User
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient

from ninja_devx import ControllerOptions
from ninja_devx.contrib.audit.api import AuditHistoryMixin, AuditLogController
from ninja_devx.contrib.audit.log import AuditMixin, diff, record, snapshot
from ninja_devx.contrib.audit.models import AuditEntry
from ninja_devx.contrib.audit.privacy import AuditPrivacy
from ninja_devx.crud import BulkCreateMixin, CRUDController, SoftDelete, SoftDeleteMixin
from ninja_devx.http.middleware import RequestIDMiddleware
from tests.testapp.models import Note, Tag

pytestmark = pytest.mark.django_db


class NoteIn(Schema):
    text: str
    priority: int = 0


class NoteOut(Schema):
    id: int
    text: str
    priority: int


class Notes(AuditHistoryMixin[Note], AuditMixin[Note], CRUDController[Note, NoteOut, NoteIn]):
    options = ControllerOptions(middleware=[RequestIDMiddleware()])
    owner_field = "owner"
    audit_redact = ("text",)

    def audit_metadata(self, request):
        return {"source": "tests"}


class TagIn(Schema):
    name: str


class TagOut(TagIn):
    id: int


class Tags(
    AuditMixin[Tag], BulkCreateMixin[Tag, TagOut, TagIn], CRUDController[Tag, TagOut, TagIn]
):
    pass


@pytest.fixture
def ada():
    return User.objects.create(username="ada")


def test_create_update_delete_are_recorded(ada):
    client = TestClient(Notes.as_router())
    created = client.post(
        "/", json={"text": "secret", "priority": 1}, user=ada, headers={"X-Request-ID": "r1"}
    )
    assert created.status_code == 201
    pk = created.json()["id"]
    client.patch(f"/{pk}", json={"priority": 5}, user=ada)
    client.patch(f"/{pk}", json={"text": "other"}, user=ada)
    assert client.delete(f"/{pk}", user=ada).status_code == 204

    create, update, redacted, delete = AuditEntry.objects.order_by("id")
    assert (create.action, create.actor, create.object_pk) == ("create", ada, str(pk))
    assert create.changes["text"] == [None, "***"]
    assert create.changes["priority"] == [None, 1]
    assert create.request_id == "r1"
    assert (create.method, create.path, create.metadata) == ("POST", "/", {"source": "tests"})
    assert update.changes == {"priority": [1, 5]}
    assert redacted.changes == {"text": ["***", "***"]}
    assert delete.action == "delete"
    assert delete.object_pk == str(pk)
    assert delete.changes["priority"] == [5, None]
    assert delete.object_repr


def test_a_failed_write_records_nothing(ada):
    client = TestClient(Tags.as_router())
    client.post("/", json={"name": "dup"})
    assert client.post("/", json={"name": "dup"}).status_code == 422
    assert AuditEntry.objects.count() == 1


def test_bulk_operations_are_recorded():
    client = TestClient(Tags.as_router())
    assert client.post("/bulk", json=[{"name": "a"}, {"name": "b"}]).status_code == 201
    assert AuditEntry.objects.filter(action="create").count() == 2


def test_history_and_log_endpoints(ada):
    ada.is_staff = True
    ada.save(update_fields=["is_staff"])
    staff = User.objects.create(username="root", is_staff=True)
    notes = TestClient(Notes.as_router())
    pk = notes.post("/", json={"text": "x"}, user=ada).json()["id"]
    notes.patch(f"/{pk}", json={"priority": 2}, user=ada)
    history = notes.get(f"/{pk}/history", user=ada).json()["items"]
    assert [item["action"] for item in history] == ["update", "create"]
    assert history[0]["model"] == "testapp.note"

    other = User.objects.create(username="bob")
    assert notes.get(f"/{pk}/history", user=other).status_code in {403, 404}

    log = TestClient(AuditLogController.as_router())
    assert log.get("/", user=other).status_code == 403
    entries = log.get("/?model=testapp.note&action=update", user=staff).json()["items"]
    assert [entry["changes"] for entry in entries] == [{"priority": [0, 2]}]
    assert log.get(f"/?actor_id={staff.pk}", user=staff).json()["items"] == []


def test_helpers(ada):
    note = Note.objects.create(owner=ada, text="a", priority=1)
    before = snapshot(note, exclude=["status"])
    note.priority = 3
    assert diff(before, snapshot(note, exclude=["status"])) == {"priority": [1, 3]}
    entry = record(None, "export", note, metadata={"rows": 1})
    assert (entry.actor, entry.request_id, entry.metadata) == (None, "", {"rows": 1})


def test_audit_privacy_redacts_before_storage_and_avoids_model_str(ada, monkeypatch):
    note = Note.objects.create(owner=ada, text="private")
    monkeypatch.setattr(Note, "__str__", lambda self: "private-model-secret")
    entry = record(
        None,
        "update",
        note,
        changes={"password": ["old", "new"], "token": [None, "secret"], "text": ["a", "b"]},
        metadata={"request": {"Authorization": "Bearer secret"}, "keep": 1},
    )
    entry.refresh_from_db()
    assert entry.changes == {"token": [None, "***"], "text": ["a", "b"]}
    assert entry.metadata == {"request": {"Authorization": "***"}, "keep": 1}
    assert entry.object_repr == f"testapp.Note:{note.pk}"
    restricted = record(
        None,
        "update",
        note,
        changes={"text": ["a", "b"], "priority": [1, 2]},
        metadata={"source": "job", "private": "secret"},
        privacy=AuditPrivacy(fields=("priority",), metadata_fields=("source",)),
    )
    assert restricted.changes == {"priority": [1, 2]}
    assert restricted.metadata == {"source": "job"}


def test_openapi_includes_history():
    api = NinjaAPI(urls_namespace="audit")
    api.add_router("/notes", Notes.as_router())
    assert "/notes/{pk}/history" in api.get_openapi_schema(path_prefix="")["paths"]


def test_history_requires_audit_access_and_paginates_without_truncation(ada):
    note = Note.objects.create(owner=ada, text="private")
    client = TestClient(Notes.as_router())
    assert client.get(f"/{note.pk}", user=ada).status_code == 200
    assert client.get(f"/{note.pk}/history", user=ada).status_code == 403
    ada.is_staff = True
    ada.save(update_fields=["is_staff"])
    template = record(None, "create", note)
    AuditEntry.objects.bulk_create(
        [
            AuditEntry(action="update", content_type=template.content_type, object_pk=str(note.pk))
            for _ in range(124)
        ]
    )
    first = client.get(f"/{note.pk}/history?page_size=999", user=ada).json()
    second = client.get(f"/{note.pk}/history?page=2&page_size=100", user=ada).json()
    assert first["count"] == 125
    assert len(first["items"]) == 100
    assert len(second["items"]) == 25
    log = TestClient(AuditLogController.as_router()).get("/?page_size=999", user=ada).json()
    assert log["count"] == 125
    assert len(log["items"]) == 100


def test_history_preserves_soft_delete_scope_even_for_staff(ada):
    class SoftNotes(SoftDeleteMixin[Note, NoteOut], Notes):
        soft_delete = SoftDelete("deleted_at", deleted_by=None)

    ada.is_staff = True
    ada.save(update_fields=["is_staff"])
    note = Note.objects.create(owner=ada, text="private")
    record(None, "create", note)
    client = TestClient(SoftNotes.as_router())
    assert client.get(f"/{note.pk}/history", user=ada).status_code == 200
    assert client.delete(f"/{note.pk}", user=ada).status_code == 204
    assert client.get(f"/{note.pk}/history", user=ada).status_code == 404
