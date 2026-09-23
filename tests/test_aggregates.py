import pytest
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx.crud import CRUDController, SoftDeleteMixin
from ninja_devx.crud.aggregates import AggregateMixin
from tests.testapp.models import Note


class NoteIn(Schema):
    text: str
    status: str = Note.Status.DRAFT
    priority: int = 0


class NoteOut(Schema):
    id: int
    text: str
    status: str
    priority: int
    archived: bool


class NoteController(AggregateMixin[Note, NoteOut], CRUDController[Note, NoteOut, NoteIn]):
    aggregate_fields = ("status",)
    aggregate_metrics = {"count": Count("id"), "total_priority": Sum("priority")}
    filter_fields = {"archived": ("exact",)}
    search_fields = ("text",)

    def perform_create(self, request, payload):
        return Note.objects.create(owner=request.user, **payload.model_dump())


class SoftNoteController(
    SoftDeleteMixin[Note, NoteOut],
    AggregateMixin[Note, NoteOut],
    CRUDController[Note, NoteOut, NoteIn],
):
    aggregate_fields = ("status",)

    def perform_create(self, request, payload):
        return Note.objects.create(owner=request.user, **payload.model_dump())


class AsyncNoteController(AggregateMixin[Note, NoteOut], CRUDController[Note, NoteOut, NoteIn]):
    mode = "async"
    aggregate_fields = ("status",)


@pytest.fixture
def owner(db):
    return User.objects.create(username="ada")


@pytest.fixture
def notes(owner):
    Note.objects.create(owner=owner, text="a", status=Note.Status.DONE, priority=2)
    Note.objects.create(owner=owner, text="b", status=Note.Status.DONE, priority=3)
    Note.objects.create(owner=owner, text="c", status=Note.Status.DRAFT, priority=1, archived=True)
    return owner


def test_stats_without_group_by_aggregates_the_whole_queryset(notes):
    client = TestClient(NoteController.as_router())
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json() == [{"count": 3}]


def test_stats_groups_by_an_allowed_field_with_chosen_metrics(notes):
    client = TestClient(NoteController.as_router())
    response = client.get("/stats?group_by=status&metrics=count&metrics=total_priority")
    assert response.status_code == 200
    rows = sorted(response.json(), key=lambda row: row["status"])
    assert rows == [
        {"status": "done", "count": 2, "total_priority": 5},
        {"status": "draft", "count": 1, "total_priority": 1},
    ]


def test_stats_applies_the_generated_filter_schema(notes):
    client = TestClient(NoteController.as_router())
    response = client.get("/stats?group_by=status&archived=true")
    assert response.json() == [{"status": "draft", "count": 1}]


def test_stats_rejects_unknown_group_by_with_422(notes):
    client = TestClient(NoteController.as_router())
    assert client.get("/stats?group_by=bogus").status_code == 422


def test_stats_rejects_unknown_metric_with_422(notes):
    client = TestClient(NoteController.as_router())
    assert client.get("/stats?metrics=bogus").status_code == 422


def test_stats_excludes_soft_deleted_rows(owner):
    controller = SoftNoteController.as_router()
    client = TestClient(controller)
    first = client.post("/", json={"text": "x", "status": "done"}, user=owner).json()
    client.post("/", json={"text": "y", "status": "done"}, user=owner)
    assert client.delete(f"/{first['id']}", user=owner).status_code == 204
    response = client.get("/stats?group_by=status")
    assert response.json() == [{"status": "done", "count": 1}]


@pytest.mark.django_db(transaction=True)
async def test_async_stats_endpoint():
    owner = await User.objects.acreate(username="ada")
    await Note.objects.acreate(owner=owner, text="a", status=Note.Status.DONE, priority=2)
    await Note.objects.acreate(owner=owner, text="b", status=Note.Status.DRAFT, priority=1)
    client = TestAsyncClient(AsyncNoteController.as_router())
    response = await client.get("/stats?group_by=status")
    rows = sorted(response.json(), key=lambda row: row["status"])
    assert rows == [{"status": "done", "count": 1}, {"status": "draft", "count": 1}]
