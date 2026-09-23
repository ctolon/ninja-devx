import pytest
from django.contrib.auth.models import User
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import ControllerConfigError, IsStaff
from ninja_devx.crud import ReadOnlyModelController
from ninja_devx.crud.async_controllers import AsyncReadOnlyModelController
from ninja_devx.crud.transitions import InvalidTransition, Transition, TransitionsMixin
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db


class NoteOut(Schema):
    id: int
    text: str
    status: str
    priority: int


on_transition_calls: list[tuple[str, str]] = []


def _reopen_guard(request: object, note: Note) -> bool:
    return note.priority > 0


class Notes(TransitionsMixin[Note, NoteOut], ReadOnlyModelController[Note, NoteOut]):
    transitions = {
        "finish": Transition(source=("draft",), target="done", permissions=[IsStaff()]),
        "reopen": Transition(source=("done",), target="draft", guard=_reopen_guard),
    }

    def on_transition(self, request: object, instance: Note, name: str) -> None:
        on_transition_calls.append((name, instance.status))


class AsyncNotes(TransitionsMixin[Note, NoteOut], AsyncReadOnlyModelController[Note, NoteOut]):
    transitions = Notes.transitions


@pytest.fixture
def people(db: None) -> tuple[User, User]:
    staff = User.objects.create(username="staff", is_staff=True)
    member = User.objects.create(username="member")
    return staff, member


def test_transition_moves_between_states(people: tuple[User, User]) -> None:
    staff, _ = people
    note = Note.objects.create(owner=staff, text="x", status="draft")
    client = TestClient(Notes.as_router())

    response = client.post(f"/{note.pk}/finish", user=staff)

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    note.refresh_from_db()
    assert note.status == "done"
    assert on_transition_calls == [("finish", "done")]


def test_transition_rejects_wrong_source_state(people: tuple[User, User]) -> None:
    staff, _ = people
    note = Note.objects.create(owner=staff, text="x", status="done")
    client = TestClient(Notes.as_router())

    response = client.post(f"/{note.pk}/finish", user=staff)

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"
    note.refresh_from_db()
    assert note.status == "done"


def test_transition_permissions_add_to_the_controllers(people: tuple[User, User]) -> None:
    _, member = people
    note = Note.objects.create(owner=member, text="x", status="draft")
    client = TestClient(Notes.as_router())

    response = client.post(f"/{note.pk}/finish", user=member)

    assert response.status_code == 403


def test_guard_blocks_transition(people: tuple[User, User]) -> None:
    staff, _ = people
    note = Note.objects.create(owner=staff, text="x", status="done", priority=0)
    client = TestClient(Notes.as_router())

    response = client.post(f"/{note.pk}/reopen", user=staff)

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


def test_guard_allows_transition(people: tuple[User, User]) -> None:
    staff, _ = people
    note = Note.objects.create(owner=staff, text="x", status="done", priority=1)
    client = TestClient(Notes.as_router())

    response = client.post(f"/{note.pk}/reopen", user=staff)

    assert response.status_code == 200
    assert response.json()["status"] == "draft"


def test_transitions_list_reflects_state_guard_and_permissions(
    people: tuple[User, User],
) -> None:
    staff, member = people
    note = Note.objects.create(owner=staff, text="x", status="draft", priority=1)
    client = TestClient(Notes.as_router())

    assert client.get(f"/{note.pk}/transitions", user=staff).json() == ["finish"]
    assert client.get(f"/{note.pk}/transitions", user=member).json() == []


@pytest.mark.django_db(transaction=True)
async def test_transitions_work_in_async_mode() -> None:
    staff = await User.objects.acreate(username="staff-async", is_staff=True)
    note = await Note.objects.acreate(owner=staff, text="x", status="draft")
    client = TestAsyncClient(AsyncNotes.as_router())

    response = await client.post(f"/{note.pk}/finish", user=staff)

    assert response.status_code == 200
    assert response.json()["status"] == "done"


def test_state_field_must_be_a_charfield() -> None:
    class Wrong(TransitionsMixin[Note, NoteOut], ReadOnlyModelController[Note, NoteOut]):
        state_field = "priority"
        transitions = {"finish": Transition(source=("draft",), target="done")}

    with pytest.raises(ControllerConfigError, match="CharField"):
        Wrong.as_router()


def test_source_and_target_must_be_declared_choices() -> None:
    class Wrong(TransitionsMixin[Note, NoteOut], ReadOnlyModelController[Note, NoteOut]):
        transitions = {"archive": Transition(source=("draft",), target="archived")}

    with pytest.raises(ControllerConfigError, match="choices"):
        Wrong.as_router()


def test_transition_name_cannot_collide_with_an_existing_route() -> None:
    class Wrong(TransitionsMixin[Note, NoteOut], ReadOnlyModelController[Note, NoteOut]):
        transitions = {"transitions": Transition(source=("draft",), target="done")}

    with pytest.raises(ControllerConfigError, match="collides"):
        Wrong.as_router()


def test_invalid_transition_is_a_conflict_domain_error() -> None:
    exc = InvalidTransition("Cannot finish from done.")
    assert (exc.http_status, exc.code) == (409, "invalid_transition")
