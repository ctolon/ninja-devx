import pytest
from django.contrib.auth.models import User
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, get
from ninja_devx.crud import CRUDController
from ninja_devx.http.conditional import ETag, conditional
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db


class NoteOut(Schema):
    id: int
    text: str


class NoteIn(Schema):
    text: str


class Notes(CRUDController[Note, NoteOut, NoteIn]):
    etag = ETag()

    def get_queryset(self, request):
        return Note.objects.all()

    def context_data(self, request):
        return {"owner": User.objects.get(username="ada")}


class StrictNotes(Notes):
    etag = ETag(require_if_match=True)


class VersionedNotes(Notes):
    etag = ETag(field="priority", weak=False)


@pytest.fixture
def note():
    ada = User.objects.create(username="ada")
    return Note.objects.create(owner=ada, text="first")


def test_retrieve_sends_etag_and_answers_304(note):
    client = TestClient(Notes.as_router())
    first = client.get(f"/{note.pk}")
    tag = first["ETag"]
    assert tag.startswith('"')
    again = client.get(f"/{note.pk}", headers={"If-None-Match": tag})
    assert again.status_code == 304
    assert again["ETag"] == tag
    note.text = "changed"
    note.save()
    assert client.get(f"/{note.pk}", headers={"If-None-Match": tag}).status_code == 200


def test_list_etag_from_body(note):
    client = TestClient(Notes.as_router())
    tag = client.get("/")["ETag"]
    assert client.get("/", headers={"If-None-Match": tag}).status_code == 304
    Note.objects.create(owner=note.owner, text="second")
    assert client.get("/", headers={"If-None-Match": tag}).status_code == 200


def test_if_match_optimistic_locking(note):
    client = TestClient(Notes.as_router())
    tag = client.get(f"/{note.pk}")["ETag"]
    updated = client.patch(f"/{note.pk}", json={"text": "mine"}, headers={"If-Match": tag})
    assert updated.status_code == 200
    new_tag = updated["ETag"]
    assert new_tag != tag
    stale = client.patch(f"/{note.pk}", json={"text": "theirs"}, headers={"If-Match": tag})
    assert (stale.status_code, stale.json()["code"]) == (412, "precondition_failed")
    assert Note.objects.get(pk=note.pk).text == "mine"
    assert client.delete(f"/{note.pk}", headers={"If-Match": tag}).status_code == 412
    assert client.delete(f"/{note.pk}", headers={"If-Match": new_tag}).status_code == 204


def test_if_match_can_be_required(note):
    client = TestClient(StrictNotes.as_router())
    response = client.put(f"/{note.pk}", json={"text": "x"})
    assert (response.status_code, response.json()["code"]) == (428, "precondition_required")
    assert (
        client.put(f"/{note.pk}", json={"text": "x"}, headers={"If-Match": "*"}).status_code == 200
    )


def test_version_field_tags(note):
    client = TestClient(VersionedNotes.as_router())
    tag = client.get(f"/{note.pk}")["ETag"]
    assert not tag.startswith("W/")
    Note.objects.filter(pk=note.pk).update(text="same version")
    assert client.get(f"/{note.pk}", headers={"If-None-Match": tag}).status_code == 304
    Note.objects.filter(pk=note.pk).update(priority=5)
    assert client.get(f"/{note.pk}", headers={"If-None-Match": tag}).status_code == 200


def test_openapi_documents_preconditions():
    from ninja import NinjaAPI

    api = NinjaAPI(urls_namespace="conditional")
    api.add_router("/notes", StrictNotes.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]
    responses = next(iter(p for k, p in paths.items() if "{" in k))["put"]["responses"]
    assert {412, 428} <= set(responses)


class Stats(Controller):
    @get("/stats", decorators=[conditional()])
    def stats(self, request):
        return {"count": Note.objects.count()}

    @get("/astats", decorators=[conditional()])
    async def astats(self, request):
        return {"count": await Note.objects.acount()}


def test_conditional_decorator_on_any_operation(note):
    client = TestClient(Stats.as_router(allow_mixed_path=True))
    tag = client.get("/stats")["ETag"]
    assert client.get("/stats", headers={"If-None-Match": tag}).status_code == 304


@pytest.mark.django_db(transaction=True)
async def test_conditional_decorator_async():
    client = TestAsyncClient(Stats.as_router(allow_mixed_path=True))
    tag = (await client.get("/astats"))["ETag"]
    assert (await client.get("/astats", headers={"If-None-Match": tag})).status_code == 304


@pytest.mark.parametrize(
    ("header", "tag", "accepted"),
    [
        ('"abc"', '"abc"', True),
        ('W/"abc"', '"abc"', False),
        ('"abc"', 'W/"abc"', False),
        ('W/"abc"', 'W/"abc"', False),
        ('"other", "abc"', '"abc"', True),
        ("*", '"abc"', True),
        ('"other"', '"abc"', False),
    ],
)
def test_if_match_uses_strong_comparison(rf, header, tag, accepted):
    from ninja_devx.http.conditional import PreconditionFailed, check_if_match

    request = rf.patch("/", HTTP_IF_MATCH=header)
    if accepted:
        check_if_match(request, tag, required=True)
    else:
        with pytest.raises(PreconditionFailed):
            check_if_match(request, tag, required=True)


def test_etag_tracks_fields_visible_to_the_current_user(note):
    from typing import Annotated

    from ninja_devx import FieldVisibility, IsStaff, VisibleTo

    class VisibleNote(FieldVisibility, Schema):
        id: int
        text: Annotated[str | None, VisibleTo(IsStaff())] = None

    class VisibleNotes(CRUDController[Note, VisibleNote, NoteIn]):
        etag = ETag()

    staff = User.objects.create(username="staff", is_staff=True)
    client = TestClient(VisibleNotes.as_router())
    first = client.get(f"/{note.pk}", user=staff)
    assert first.json()["text"] == "first"
    Note.objects.filter(pk=note.pk).update(text="new")
    changed = client.get(f"/{note.pk}", user=staff, headers={"If-None-Match": first["ETag"]})
    assert changed.status_code == 200
    assert changed.json()["text"] == "new"
    assert changed["ETag"] != first["ETag"]


def test_conditional_write_holds_transaction_and_requests_row_lock(note, monkeypatch):
    from django.db import connection

    observed = []
    original = Notes.get_object

    def get_object(self, request, lookup, *, lock=False):
        observed.append((lock, connection.in_atomic_block))
        return original(self, request, lookup, lock=lock)

    monkeypatch.setattr(Notes, "get_object", get_object)
    client = TestClient(Notes.as_router())
    assert client.patch(f"/{note.pk}", json={"text": "updated"}).status_code == 200
    assert client.delete(f"/{note.pk}").status_code == 204
    assert observed == [(True, True), (True, True)]
