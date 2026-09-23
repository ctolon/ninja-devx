import pytest
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx.contrib.grants.backends import GrantsBackend
from ninja_devx.crud import CRUDController, ObjectSharingMixin
from ninja_devx.security.object_permissions import (
    DjangoBackend,
    GuardianBackend,
    ObjectPermissions,
    assign_perm,
    get_backend,
    get_objects_for_user,
    get_perms,
    grants_for,
    remove_perm,
)
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db

GUARDIAN = {"OBJECT_PERMISSION_BACKEND": "ninja_devx.security.object_permissions.GuardianBackend"}


class NoteOut(Schema):
    id: int
    text: str


class NoteIn(Schema):
    text: str


class Notes(ObjectSharingMixin[Note], CRUDController[Note, NoteOut, NoteIn]):
    object_permissions = ObjectPermissions()
    shareable_permissions = ("view", "change")

    def context_data(self, request):
        return {"owner": request.user}


@pytest.fixture
def people():
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    note = Note.objects.create(owner=ada, text="shared?")
    other = Note.objects.create(owner=ada, text="private")
    return ada, bob, note, other


def fresh(user: User) -> User:
    return User.objects.get(pk=user.pk)  # permission caches live on the user object


@pytest.mark.parametrize("backend", [None, GUARDIAN], ids=["grants", "guardian"])
def test_backends_grant_check_filter_and_revoke(people, backend):
    _, bob, note, other = people
    with override_settings(NINJA_DEVX=backend or {}):
        expected = GuardianBackend if backend else GrantsBackend
        assert isinstance(get_backend(), expected)
        assign_perm("testapp.view_note", bob, note)
        assert fresh(bob).has_perm("testapp.view_note", note)  # through AUTHENTICATION_BACKENDS
        assert get_perms(fresh(bob), note, ["testapp.view_note", "testapp.change_note"]) == [
            "testapp.view_note"
        ]
        assert list(get_objects_for_user(fresh(bob), "testapp.view_note", Note.objects.all())) == [
            note
        ]
        team = Group.objects.create(name="team")
        bob.groups.add(team)
        assign_perm("testapp.change_note", team, other)
        assert get_backend().has_perm(fresh(bob), "testapp.change_note", other)
        grants = {(g.user_id, g.group_id): g.permissions for g in grants_for(other)}
        assert grants == {(None, team.pk): ("testapp.change_note",)}
        remove_perm("testapp.view_note", bob, note)
        assert not get_backend().has_perm(fresh(bob), "testapp.view_note", note)


def test_superusers_see_everything(people):
    _, _, note, other = people
    root = User.objects.create(username="root", is_superuser=True)
    assert set(get_objects_for_user(root, "testapp.view_note", Note.objects.all())) == {note, other}


def test_django_backend_checks_but_cannot_filter(people):
    _, bob, note, _ = people
    assign_perm("testapp.view_note", bob, note)
    backend = DjangoBackend()
    assert backend.has_perm(fresh(bob), "testapp.view_note", note)
    with pytest.raises(ImproperlyConfigured, match="cannot filter"):
        backend.filter_queryset(bob, ["testapp.view_note"], Note.objects.all())


def test_controller_hides_and_forbids_by_method(people):
    _, bob, note, other = people
    client = TestClient(Notes.as_router())
    assert client.get("/", user=bob).json() == []
    assert client.get(f"/{note.pk}", user=bob).status_code == 404

    assign_perm("testapp.view_note", bob, note)
    assert [n["id"] for n in client.get("/", user=fresh(bob)).json()] == [note.pk]
    assert client.get(f"/{note.pk}", user=fresh(bob)).status_code == 200
    assert client.put(f"/{note.pk}", json={"text": "x"}, user=fresh(bob)).status_code == 403
    assert client.get(f"/{other.pk}", user=fresh(bob)).status_code == 404

    assign_perm("testapp.change_note", bob, note)
    assert client.put(f"/{note.pk}", json={"text": "x"}, user=fresh(bob)).status_code == 200
    assert client.delete(f"/{note.pk}", user=fresh(bob)).status_code == 403


def test_model_permissions_and_visible_denials(people):
    _, bob, note, _ = people

    class GlobalNotes(Notes):
        object_permissions = ObjectPermissions(model_permissions=True, hide_forbidden=False)

    bob.user_permissions.add(Permission.objects.get(codename="view_note"))
    client = TestClient(GlobalNotes.as_router())
    assert len(client.get("/", user=fresh(bob)).json()) == 2
    assert client.put(f"/{note.pk}", json={"text": "x"}, user=fresh(bob)).status_code == 403


def test_sharing_endpoints(people):
    ada, bob, note, _ = people
    for perm in ("view", "change"):
        assign_perm(f"testapp.{perm}_note", ada, note)
    client = TestClient(Notes.as_router())

    shared = client.put(
        f"/{note.pk}/permissions",
        json={"user_id": bob.pk, "permissions": ["view"]},
        user=fresh(ada),
    )
    assert shared.status_code == 200
    assert {
        "user_id": bob.pk,
        "group_id": None,
        "permissions": ["testapp.view_note"],
    } in shared.json()
    assert client.get(f"/{note.pk}", user=fresh(bob)).status_code == 200

    assert client.get(f"/{note.pk}/permissions", user=fresh(bob)).status_code == 403
    invalid = client.put(
        f"/{note.pk}/permissions",
        json={"user_id": bob.pk, "permissions": ["delete"]},
        user=fresh(ada),
    )
    assert invalid.status_code == 422
    both = client.put(
        f"/{note.pk}/permissions",
        json={"user_id": bob.pk, "group_id": 1, "permissions": []},
        user=fresh(ada),
    )
    assert both.status_code == 422

    revoked = client.post(
        f"/{note.pk}/permissions/revoke", json={"user_id": bob.pk}, user=fresh(ada)
    )
    assert all(grant["user_id"] != bob.pk for grant in revoked.json())
    assert client.get(f"/{note.pk}", user=fresh(bob)).status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_async_object_permissions():
    from asgiref.sync import sync_to_async

    ada = await User.objects.acreate(username="ada")
    bob = await User.objects.acreate(username="bob")
    note = await Note.objects.acreate(owner=ada, text="n")
    await sync_to_async(assign_perm)("testapp.view_note", bob, note)

    class AsyncNotes(Notes):
        mode = "async"

    client = TestAsyncClient(AsyncNotes.as_router())
    bob = await User.objects.aget(pk=bob.pk)
    assert [n["id"] for n in (await client.get("/", user=bob)).json()] == [note.pk]
    assert (await client.get(f"/{note.pk}", user=bob)).status_code == 200
    assert (await client.delete(f"/{note.pk}", user=bob)).status_code == 403


def test_validate_holder_restricts_sharing(people):
    from ninja.errors import HttpError

    ada, bob, note, _ = people
    assign_perm("testapp.change_note", ada, note)
    assign_perm("testapp.view_note", ada, note)

    class StaffOnlySharing(Notes):
        def validate_holder(self, request, obj, holder):
            if not holder.is_staff:
                raise HttpError(422, "staff only")

    client = TestClient(StaffOnlySharing.as_router())
    body = {"user_id": bob.pk, "permissions": ["view"]}
    response = client.put(f"/{note.pk}/permissions", json=body, user=fresh(ada))
    assert (response.status_code, response.json()["detail"]) == (422, "staff only")
    bob.is_staff = True
    bob.save()
    assert client.put(f"/{note.pk}/permissions", json=body, user=fresh(ada)).status_code == 200
