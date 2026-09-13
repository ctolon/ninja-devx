from dataclasses import dataclass

import pytest
from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Container, Controller, ControllerConfigError, as_permission, post, use_case
from ninja_devx.layers import NotFound
from tests.testapp.models import Note


class NoteIn(Schema):
    text: str

    def to_command(self) -> "AddNote":
        return AddNote(text=self.text.strip())


class NoteOut(Schema):
    id: int
    text: str


@dataclass(frozen=True)
class AddNote:
    text: str


class Clock:
    def now(self) -> str:
        return "noon"


class AddNoteHandler:
    """Add a note owned by the first user."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def __call__(self, command: AddNote) -> Note:
        owner = User.objects.first()
        if owner is None:
            raise NotFound("no users")
        return Note.objects.create(owner=owner, text=f"{command.text} at {self.clock.now()}")


class AsyncAddNoteHandler(AddNoteHandler):
    async def __call__(self, command: AddNote) -> Note:  # type: ignore[override]
        owner = await User.objects.afirst()
        assert owner is not None
        return await Note.objects.acreate(owner=owner, text=command.text)


class Notes(Controller):
    add = use_case(
        post("/", response={201: NoteOut}), AddNoteHandler, command=NoteIn.to_command, status=201
    )


class AsyncNotes(Controller):
    add = use_case(post("/", response=NoteOut), AsyncAddNoteHandler, command=NoteIn.to_command)


@pytest.mark.django_db
def test_use_case_maps_the_payload_and_calls_the_handler():
    User.objects.create(username="ada")
    client = TestClient(Notes.as_router(container=Container()))
    response = client.post("/", json={"text": "  hello "})
    assert response.status_code == 201
    assert response.json()["text"] == "hello at noon"


@pytest.mark.django_db
def test_domain_errors_from_use_cases_are_mapped():
    response = TestClient(Notes.as_router(container=Container())).post("/", json={"text": "x"})
    assert (response.status_code, response.json()["code"]) == (404, "not_found")


@pytest.mark.django_db(transaction=True)
async def test_async_use_cases():
    await User.objects.acreate(username="ada")
    client = TestAsyncClient(AsyncNotes.as_router(container=Container()))
    response = await client.post("/", json={"text": "async"})
    assert (response.status_code, response.json()["text"]) == (200, "async")


def test_use_case_documents_the_payload_and_needs_a_container():
    api = NinjaAPI(urls_namespace="use-cases")
    api.add_router("/notes", Notes.as_router(container=Container()))
    operation = api.get_openapi_schema(path_prefix="")["paths"]["/notes/"]["post"]
    assert operation["description"] == "Add a note owned by the first user."
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/NoteIn"
    }
    with pytest.raises(ControllerConfigError, match="needs as_router"):
        Notes.as_router()


def test_command_mapper_must_be_annotated():
    with pytest.raises(ControllerConfigError, match="annotate"):
        use_case(post("/"), AddNoteHandler, command=lambda payload: payload)


# --- Policies with a user class ----------------------------------------------------------


class OwnsNote:
    def allows(self, subject: User, obj: Note, /) -> bool:
        return obj.owner_id == subject.pk


class NoteByOwner(Controller):
    @post("/{pk}", permissions=[as_permission(OwnsNote(), User)])
    def touch(self, request: HttpRequest, pk: int) -> dict[str, bool]:
        self.check_object_permissions(request, Note.objects.get(pk=pk))
        return {"ok": True}


@pytest.mark.django_db
def test_as_permission_with_a_user_class():
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    note = Note.objects.create(owner=ada, text="x")
    client = TestClient(NoteByOwner.as_router())
    assert client.post(f"/{note.pk}", user=ada).status_code == 200
    assert client.post(f"/{note.pk}", user=bob).status_code == 403
