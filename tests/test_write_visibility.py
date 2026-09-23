from types import SimpleNamespace
from typing import Annotated

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from ninja import Schema
from ninja.testing import TestClient

from ninja_devx import IsAuthenticated, IsStaff, WriteVisibleTo
from ninja_devx.crud import CRUDController
from tests.testapp.models import Note


class NoteOut(Schema):
    id: int
    text: str


class NoteIn(Schema):
    text: str
    priority: Annotated[int, WriteVisibleTo(IsStaff())] = 0


class Notes(CRUDController[Note, NoteOut, NoteIn]):
    def perform_create(self, request, payload):
        return Note.objects.create(owner=request.user, **payload.model_dump())

    def perform_update(self, request, instance, data):
        for name, value in data.items():
            setattr(instance, name, value)
        instance.save()
        return instance


@pytest.fixture
def people(db):
    staff = User.objects.create(username="staff", is_staff=True)
    member = User.objects.create(username="member")
    note = Note.objects.create(owner=member, text="original")
    return staff, member, note


@pytest.mark.django_db
def test_write_visibility_rejects_forbidden_field_on_create(people):
    _, member, _ = people
    client = TestClient(Notes.as_router())
    response = client.post("/", json={"text": "x", "priority": 5}, user=member)
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


@pytest.mark.django_db
def test_write_visibility_allows_staff_and_omitted_fields(people):
    staff, member, _ = people
    client = TestClient(Notes.as_router())
    assert client.post("/", json={"text": "by-staff", "priority": 5}, user=staff).status_code == 201
    assert client.post("/", json={"text": "plain"}, user=member).status_code == 201


@pytest.mark.django_db
def test_write_visibility_rejects_forbidden_field_on_update(people):
    _, member, note = people
    client = TestClient(Notes.as_router())
    response = client.patch(f"/{note.pk}", json={"priority": 9}, user=member)
    assert response.status_code == 403
    note.refresh_from_db()
    assert note.priority == 0


def test_write_visible_to_requires_permissions_and_fails_closed():
    with pytest.raises(TypeError, match="at least one"):
        WriteVisibleTo()
    assert WriteVisibleTo(IsStaff()).allows(None) is False


def test_write_visible_to_evaluates_permission_combinators():
    request = RequestFactory().get("/x")
    request.user = SimpleNamespace(is_staff=True, is_authenticated=True, is_active=True)
    assert WriteVisibleTo(IsStaff() & IsAuthenticated()).allows(request) is True
    assert WriteVisibleTo(IsStaff() | IsAuthenticated()).allows(request) is True
    assert WriteVisibleTo(~IsStaff()).allows(request) is False
