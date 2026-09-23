import enum

import pytest
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx.crud import CRUDController, ModelController
from ninja_devx.crud.meta import MetaMixin
from ninja_devx.testing.clients import assert_max_queries
from tests.testapp.models import Note


class Kind(enum.Enum):
    NORMAL = "normal"
    URGENT = "urgent"


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
    kind: Kind = Kind.NORMAL

    @staticmethod
    def resolve_kind(obj: Note) -> Kind:
        return Kind.NORMAL


class NoteController(MetaMixin[Note], CRUDController[Note, NoteOut, NoteIn]):
    filter_fields = {"status": ("exact",)}
    ordering_fields = ("priority",)
    search_fields = ("text",)

    def perform_create(self, request, payload):
        return Note.objects.create(owner=request.user, **payload.model_dump())


class BareNoteController(MetaMixin[Note], ModelController[Note]):
    pass


class AsyncBareNoteController(MetaMixin[Note], ModelController[Note]):
    mode = "async"


def test_meta_reports_model_choices_with_translated_labels():
    client = TestClient(NoteController.as_router())
    response = client.get("/meta")
    assert response.status_code == 200
    body = response.json()
    status = body["output"]["status"]
    assert status["choices"] == [
        {"value": "draft", "label": "Draft"},
        {"value": "done", "label": "Done"},
    ]
    assert status["type"] == "str"
    assert status["max_length"] == 10


def test_meta_reports_enum_choices_from_the_schema_type():
    client = TestClient(NoteController.as_router())
    body = client.get("/meta").json()
    kind = body["output"]["kind"]
    assert {choice["value"] for choice in kind["choices"]} == {"normal", "urgent"}


def test_meta_marks_output_only_fields_read_only():
    client = TestClient(NoteController.as_router())
    body = client.get("/meta").json()
    assert body["output"]["id"]["read_only"] is True
    assert body["output"]["id"]["required"] is True
    assert body["input"]["text"]["read_only"] is False


def test_meta_reports_filter_ordering_and_search_field_names():
    client = TestClient(NoteController.as_router())
    body = client.get("/meta").json()
    assert body["filter_fields"] == ["status"]
    assert body["ordering_fields"] == ["priority"]
    assert body["search_fields"] == ["text"]


def test_meta_is_cached_per_controller_class():
    assert NoteController.controller_meta() is NoteController.controller_meta()


@pytest.mark.django_db
def test_meta_needs_no_database_access():
    client = TestClient(NoteController.as_router())
    with assert_max_queries(0):
        assert client.get("/meta").status_code == 200


def test_meta_of_a_controller_without_schemas_is_empty():
    client = TestClient(BareNoteController.as_router())
    body = client.get("/meta").json()
    assert body == {
        "input": {},
        "output": {},
        "filter_fields": [],
        "ordering_fields": [],
        "search_fields": [],
    }


async def test_async_meta_endpoint():
    client = TestAsyncClient(AsyncBareNoteController.as_router())
    response = await client.get("/meta")
    assert response.status_code == 200
    assert response.json()["input"] == {}


@pytest.mark.django_db
def test_meta_does_not_shadow_the_pk_lookup():
    from django.contrib.auth.models import User

    user = User.objects.create(username="ada")
    note = Note.objects.create(owner=user, text="hi")
    client = TestClient(NoteController.as_router())
    response = client.get(f"/{note.pk}")
    assert response.status_code == 200
    assert response.json()["text"] == "hi"
