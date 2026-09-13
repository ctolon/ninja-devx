from typing import Annotated

import pytest
from django.contrib.auth.models import User
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient

from ninja_devx import IsOwner, IsStaff
from ninja_devx.crud import ReadOnlyModelController
from ninja_devx.serialization.visibility import FieldVisibility, VisibleTo
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db


class PlainOut(Schema):
    id: int
    text: str
    priority: Annotated[int | None, VisibleTo(IsStaff())] = None


class RichOut(FieldVisibility, Schema):
    id: int
    text: str
    priority: Annotated[int | None, VisibleTo(IsStaff(), hidden="omit")] = None
    status: Annotated[str | None, VisibleTo(IsStaff() | IsOwner("owner"))] = None


class PlainNotes(ReadOnlyModelController[Note, PlainOut]):
    pass


class RichNotes(ReadOnlyModelController[Note, RichOut]):
    pass


@pytest.fixture
def people():
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    boss = User.objects.create(username="boss", is_staff=True)
    note = Note.objects.create(owner=ada, text="x", priority=3)
    return ada, bob, boss, note


def test_request_level_rules_on_any_schema(people):
    ada, _, boss, note = people
    client = TestClient(PlainNotes.as_router())
    assert client.get(f"/{note.pk}", user=ada).json()["priority"] is None
    assert client.get(f"/{note.pk}", user=boss).json()["priority"] == 3
    assert client.get("/", user=boss).json()[0]["priority"] == 3


def test_omit_and_object_level_rules_with_the_mixin(people):
    ada, bob, boss, note = people
    client = TestClient(RichNotes.as_router())
    as_owner = client.get(f"/{note.pk}", user=ada).json()
    assert "priority" not in as_owner
    assert as_owner["status"] == "draft"
    as_stranger = client.get(f"/{note.pk}", user=bob).json()
    assert as_stranger["status"] is None
    assert client.get("/", user=boss).json()[0] == {
        "id": note.pk,
        "text": "x",
        "priority": 3,
        "status": "draft",
    }


def test_object_level_rules_fail_closed_without_the_mixin(people):
    ada, _, _, note = people

    class NoMixinOut(Schema):
        id: int
        status: Annotated[str | None, VisibleTo(IsOwner("owner"))] = None

    class Notes(ReadOnlyModelController[Note, NoMixinOut]):
        pass

    assert TestClient(Notes.as_router()).get(f"/{note.pk}", user=ada).json()["status"] is None


def test_openapi_keeps_the_field_optional():
    api = NinjaAPI(urls_namespace="visibility")
    api.add_router("/notes", RichNotes.as_router())
    schema = api.get_openapi_schema(path_prefix="")["components"]["schemas"]["RichOut"]
    assert "priority" in schema["properties"]
    assert "priority" not in schema.get("required", [])


def test_visible_to_needs_a_permission():
    with pytest.raises(TypeError):
        VisibleTo()
