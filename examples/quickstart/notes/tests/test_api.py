import pytest
from django.contrib.auth.models import User
from ninja.testing import TestClient

from config.urls import api
from notes.models import Note

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> TestClient:
    return TestClient(api)


@pytest.fixture
def ada() -> User:
    return User.objects.create(username="ada")


def test_notes_crud(client: TestClient, ada: User) -> None:
    created = client.post("/notes/", json={"title": "Buy milk"}, user=ada)
    assert created.status_code == 201
    note_id = created.json()["id"]

    page = client.get("/notes/?search=milk&done=false", user=ada).json()
    assert page["count"] == 1

    completed = client.post(f"/notes/{note_id}/complete", user=ada)
    assert completed.json()["done"] is True

    assert client.patch(f"/notes/{note_id}", json={"body": "2 liters"}, user=ada).status_code == 200
    assert client.delete(f"/notes/{note_id}", user=ada).status_code == 204


def test_notes_are_private(client: TestClient, ada: User) -> None:
    bob = User.objects.create(username="bob")
    note = Note.objects.create(owner=ada, title="secret")
    assert client.get("/notes/", user=bob).json()["count"] == 0
    assert client.get(f"/notes/{note.pk}", user=bob).status_code == 404
    assert client.get("/notes/").status_code == 401


def test_validation_and_health(client: TestClient, ada: User) -> None:
    assert client.post("/notes/", json={"title": ""}, user=ada).status_code == 422
    assert client.get("/health/").json() == {"status": "ok"}


def test_checks_pass() -> None:
    from django.core.management import call_command

    call_command("check", fail_level="WARNING")
    call_command("devx_scaffold", "--check")
