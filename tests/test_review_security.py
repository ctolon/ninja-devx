"""Cross-feature security contracts identified during the pre-release review."""

import asyncio
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from django.utils import timezone
from ninja import NinjaAPI, Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Container, ControllerConfigError, IsOwner, IsStaff
from ninja_devx._permission_eval import denied
from ninja_devx._permission_eval_async import adenied
from ninja_devx.contrib.apikeys.api import APIKeyController
from ninja_devx.contrib.apikeys.auth import APIKeyAuth, create_api_key
from ninja_devx.contrib.apikeys.models import APIKey
from ninja_devx.crud import BulkDestroyMixin, CRUDController, SoftDeleteMixin
from ninja_devx.crud.transfer import ExportMixin
from ninja_devx.http.throttling import AnonRateThrottle
from tests.testapp.models import Note


@pytest.mark.parametrize("staff", [False, True])
@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
@pytest.mark.parametrize("expression", ["or", "and", "not_owner", "not_or", "nested"])
async def test_permission_boolean_algebra_across_request_and_object(
    staff, owner, method, expression
):
    request = RequestFactory().generic(method, "/")
    request.user = SimpleNamespace(pk=1, is_authenticated=True, is_staff=staff)
    obj = SimpleNamespace(owner_id=1 if owner else 2)
    expressions = {
        "or": (IsStaff() | IsOwner(), staff or owner),
        "and": (IsStaff() & IsOwner(), staff and owner),
        "not_owner": (~IsOwner(), not owner),
        "not_or": (~(IsStaff() | IsOwner()), not (staff or owner)),
        "nested": ((IsStaff() | IsOwner()) & ~IsStaff(), owner and not staff),
    }
    permission, expected = expressions[expression]
    # Preflight must not reject a decision which needs an object and will allow it.
    if expected:
        assert denied(permission, request, None) is None
        assert await adenied(permission, request, None) is None
    assert (denied(permission, request, (obj,)) is None) is expected
    assert (await adenied(permission, request, (obj,)) is None) is expected


class NoteOut(Schema):
    id: int
    text: str


class NoteIn(Schema):
    text: str


class AllNotes:
    def __call__(self, filters):
        return Note.objects.all()


class ScopedNotes(
    ExportMixin[Note, NoteOut],
    SoftDeleteMixin[Note, NoteOut],
    BulkDestroyMixin[Note],
    CRUDController[Note, NoteOut, NoteIn],
):
    selector_class = AllNotes
    owner_field = "owner"
    scope_queryset_to_owner = True
    # Reuse a user FK to exercise both tenant and owner scoping on one model.
    tenant_field = "owner"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_selector_preserves_tenant_owner_soft_delete_and_export(mode):
    ada = await User.objects.acreate(username="ada")
    bob = await User.objects.acreate(username="bob")
    await Note.objects.acreate(owner=ada, text="visible")
    await Note.objects.acreate(owner=ada, text="deleted", deleted_at=timezone.now())
    await Note.objects.acreate(owner=bob, text="private")

    class Notes(ScopedNotes):
        pass

    Notes.mode = mode
    router = Notes.as_router()
    if mode == "async":
        client = TestAsyncClient(router)
        response = await client.get("/", user=ada, tenant=ada)
        export = await client.get("/export?format=jsonl", user=ada, tenant=ada)
        missing = await client.get("/", user=ada)
    else:
        from asgiref.sync import sync_to_async

        client = TestClient(router)
        response = await sync_to_async(client.get)("/", user=ada, tenant=ada)

        # Consume sync streaming inside the thread which owns its DB connection.
        def fetch_export():
            return client.get("/export?format=jsonl", user=ada, tenant=ada).content

        export = SimpleNamespace(content=await sync_to_async(fetch_export)())
        missing = await sync_to_async(client.get)("/", user=ada)
    assert [item["text"] for item in response.json()] == ["visible"]
    assert b"visible" in export.content
    assert b"deleted" not in export.content
    assert b"private" not in export.content
    assert missing.status_code == 403


@pytest.mark.django_db
def test_materialized_selector_rejected_instead_of_bypassing_scoping():
    user = User.objects.create(username="ada")

    class ListSelector:
        def __call__(self, filters):
            return []

    class Notes(ScopedNotes):
        selector_class = ListSelector

    with pytest.raises(ControllerConfigError, match="must return a QuerySet"):
        TestClient(Notes.as_router()).get("/", user=user, tenant=user)


@pytest.mark.django_db
@pytest.mark.parametrize("scopes", [["orders:read"], ["*"]])
def test_api_keys_cannot_manage_credentials(scopes):
    user = User.objects.create(username="ada")
    key, raw = create_api_key(user, "limited", scopes=scopes)
    api = NinjaAPI(auth=APIKeyAuth(), urls_namespace="review-key-management")
    api.add_router("/keys", APIKeyController.as_router())
    client = TestClient(api)
    headers = {"X-API-Key": raw}
    assert (
        client.post(
            "/keys/", json={"name": "elevated", "scopes": ["*"]}, headers=headers
        ).status_code
        == 403
    )
    assert client.get("/keys/", headers=headers).status_code == 403
    assert client.delete(f"/keys/{key.pk}", headers=headers).status_code == 403
    assert APIKey.objects.count() == 1
    key.refresh_from_db()
    assert key.revoked_at is None


@pytest.mark.parametrize(
    "rate", ["10/0min", "0/min", "-1/min", "1/-3min", "1/00min", "1/m1in", "1/", "1/min "]
)
def test_invalid_throttle_rates_fail_at_configuration(rate):
    with pytest.raises(ImproperlyConfigured, match="Invalid throttle rate"):
        AnonRateThrottle(rate)


@pytest.mark.django_db
def test_duplicate_bulk_delete_is_validation_error_and_preserves_rows():
    user = User.objects.create(username="ada")
    note = Note.objects.create(owner=user, text="keep")

    class Notes(BulkDestroyMixin[Note], CRUDController[Note, NoteOut, NoteIn]):
        pass

    response = TestClient(Notes.as_router()).post("/bulk-delete", json={"pks": [note.pk, note.pk]})
    assert response.status_code == 422
    assert Note.objects.filter(pk=note.pk).exists()


async def test_nested_singletons_and_parallel_scope_build_once():
    class Connection:
        pass

    class Service:
        def __init__(self, connection: Connection):
            self.connection = connection

    created = []
    closed = []

    async def connection():
        await asyncio.sleep(0)
        value = Connection()
        created.append(value)
        try:
            yield value
        finally:
            closed.append(value)

    container = Container()
    container.singleton(Connection, connection)
    container.singleton(Service)
    a, b = await asyncio.wait_for(
        asyncio.gather(container.aresolve(Service), container.aresolve(Service)), timeout=1
    )
    assert a is b
    assert a.connection is created[0]
    assert len(created) == 1
    await container.aclose()
    assert closed == created

    container = Container()
    container.scoped(Connection, connection)
    async with container.scope() as scope:
        a, b = await asyncio.wait_for(
            asyncio.gather(scope.aresolve(Connection), scope.aresolve(Connection)), timeout=1
        )
        assert a is b
    assert len(created) == len(closed) == 2


async def test_cancelled_waiter_does_not_cancel_shared_factory():
    class Service:
        pass

    started = asyncio.Event()
    finish = asyncio.Event()

    async def build():
        started.set()
        await finish.wait()
        return Service()

    container = Container()
    container.singleton(Service, build)
    builder = asyncio.create_task(container.aresolve(Service))
    await started.wait()
    waiter = asyncio.create_task(container.aresolve(Service))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    finish.set()
    result = await asyncio.wait_for(builder, timeout=1)
    assert await container.aresolve(Service) is result


async def test_failed_factory_can_be_retried():
    class Service:
        pass

    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("retry me")
        return Service()

    container = Container()
    container.singleton(Service, build)
    with pytest.raises(RuntimeError, match="retry me"):
        await container.aresolve(Service)
    assert isinstance(await container.aresolve(Service), Service)
    assert calls == 2
