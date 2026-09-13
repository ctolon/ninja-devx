import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from ninja import Schema, Status
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, idempotent, post
from ninja_devx.idempotency.models import IdempotencyRecord
from ninja_devx.idempotency.store import Claim, acquire

pytestmark = pytest.mark.django_db(transaction=True)


class Charge(Schema):
    amount: int


class PaymentController(Controller):
    @post("/", response={201: dict[str, int]}, decorators=[idempotent()])
    def charge(self, request, payload: Charge):
        user = User.objects.create(username=f"payment-{User.objects.count()}")
        return Status(201, {"payment": user.pk, "amount": payload.amount})

    @post("/strict", decorators=[idempotent(required=True)])
    def strict(self, request):
        return {"ok": True}

    @post("/fail", response={500: dict[str, bool]}, decorators=[idempotent()])
    def fail(self, request):
        return Status(500, {"error": True})

    @post("/async", decorators=[idempotent()])
    async def charge_async(self, request):
        return {"ok": True}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def charge(client, key, amount=10):
    return client.post("/", json={"amount": amount}, headers={"Idempotency-Key": key})


def test_repeated_keys_replay_the_first_response():
    client = TestClient(PaymentController.as_router())
    first = charge(client, "abc")
    second = charge(client, "abc")
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second["Idempotent-Replayed"] == "true"
    assert User.objects.count() == 1
    assert charge(client, "other").json()["payment"] != first.json()["payment"]


def test_reusing_a_key_with_another_body_is_rejected():
    client = TestClient(PaymentController.as_router())
    charge(client, "abc", amount=10)
    assert charge(client, "abc", amount=99).status_code == 422


def test_required_header():
    client = TestClient(PaymentController.as_router())
    assert client.post("/strict").status_code == 400
    assert client.post("/strict", headers={"Idempotency-Key": "k"}).status_code == 200


def test_errors_are_replayed_without_repeating_possible_side_effects():
    client = TestClient(PaymentController.as_router())
    assert client.post("/fail", headers={"Idempotency-Key": "k"}).status_code == 500
    response = client.post("/fail", headers={"Idempotency-Key": "k"})
    assert response.status_code == 500
    assert response["Idempotent-Replayed"] == "true"


def test_concurrent_requests_with_one_key_conflict():
    claim = acquire("abc", "body", database="default", ttl=60)
    assert isinstance(claim, Claim)
    assert acquire("abc", "body", database="default", ttl=60).status_code == 409


@pytest.mark.django_db(transaction=True)
async def test_async_operations():
    client = TestAsyncClient(PaymentController.as_router())
    first = await client.post("/async", headers={"Idempotency-Key": "k"})
    second = await client.post("/async", headers={"Idempotency-Key": "k"})
    assert first.json() == second.json() == {"ok": True}
    assert second["Idempotent-Replayed"] == "true"


def test_users_tenants_and_custom_session_cookie_partition_keys(settings):
    settings.SESSION_COOKIE_NAME = "custom_session"
    client = TestClient(PaymentController.as_router())
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    responses = []
    for user, tenant, cookie in [(ada, 1, "a"), (bob, 1, "a"), (ada, 2, "a"), (ada, 1, "b")]:
        response = client.post(
            "/",
            json={"amount": 10},
            user=user,
            tenant=tenant,
            COOKIES={"custom_session": cookie},
            headers={"Idempotency-Key": "same"},
        )
        responses.append(response.json()["payment"])
    assert len(set(responses)) == 4


@pytest.mark.parametrize("header_name", ["X-API-Key", "X-Alternate-Key"])
def test_api_credentials_are_isolated_and_revoked_key_cannot_replay(header_name):
    from ninja import NinjaAPI

    from ninja_devx.contrib.apikeys.auth import APIKeyAuth, create_api_key, revoke_api_key

    owner = User.objects.create(username="owner")
    key1, raw1 = create_api_key(owner, "first")
    _, raw2 = create_api_key(owner, "second")

    class Auth(APIKeyAuth):
        param_name = header_name

    api = NinjaAPI(auth=Auth(), urls_namespace="idempotency-key-isolation")
    api.add_router("/pay", PaymentController.as_router())
    client = TestClient(api)
    first = client.post(
        "/pay/", json={"amount": 10}, headers={"Idempotency-Key": "same", header_name: raw1}
    )
    second = client.post(
        "/pay/", json={"amount": 10}, headers={"Idempotency-Key": "same", header_name: raw2}
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["payment"] != second.json()["payment"]
    revoke_api_key(key1)
    assert (
        client.post(
            "/pay/", json={"amount": 10}, headers={"Idempotency-Key": "same", header_name: raw1}
        ).status_code
        == 401
    )


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_request_permission_is_checked_before_replay(asynchronous):
    from asgiref.sync import sync_to_async

    from ninja_devx import IsStaff

    class Payments(PaymentController):
        from ninja_devx import ControllerOptions

        options = ControllerOptions(permissions=[IsStaff()])

    user = await User.objects.acreate(username="staff", is_staff=True)
    headers = {"Idempotency-Key": "same"}
    if asynchronous:
        client = TestAsyncClient(Payments.as_router())

        def call():
            return client.post("/async", user=user, headers=headers)
    else:
        client = TestClient(Payments.as_router())

        def call():
            return sync_to_async(client.post)("/strict", user=user, headers=headers)

    assert (await call()).status_code == 200
    user.is_staff = False
    assert (await call()).status_code == 403


def test_model_owner_is_rechecked_before_replay():
    from ninja_devx.crud import CRUDController
    from tests.test_review_security import NoteIn, NoteOut
    from tests.testapp.models import Note

    class Notes(CRUDController[Note, NoteOut, NoteIn]):
        owner_field = "owner"

        @post("/{pk}/touch", response=NoteOut, decorators=[idempotent()])
        def touch(self, request, pk: int):
            return self.get_object(request, pk)

    owner = User.objects.create(username="owner")
    other = User.objects.create(username="other")
    note = Note.objects.create(owner=owner, text="private")
    client = TestClient(Notes.as_router())
    headers = {"Idempotency-Key": "same"}
    assert client.post(f"/{note.pk}/touch", user=owner, headers=headers).status_code == 200
    Note.objects.filter(pk=note.pk).update(owner=other)
    assert client.post(f"/{note.pk}/touch", user=owner, headers=headers).status_code == 403


def test_inflight_claim_never_expires_and_old_owner_cannot_finalize_new_claim():
    from datetime import timedelta

    from django.http import HttpResponse
    from django.utils import timezone

    first = acquire("abc", "body", database="default", ttl=60)
    assert isinstance(first, Claim)
    IdempotencyRecord.objects.filter(pk="abc").update(
        created_at=timezone.now() - timedelta(days=30)
    )
    assert acquire("abc", "body", database="default", ttl=60).status_code == 409
    first.finish(HttpResponse(b"first", headers={"ETag": '"v1"', "Retry-After": "10"}))
    replay = acquire("abc", "body", database="default", ttl=60)
    assert replay["ETag"] == '"v1"'
    assert replay["Retry-After"] == "10"
    IdempotencyRecord.objects.filter(pk="abc").update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    next_claim = acquire("abc", "new", database="default", ttl=60)
    assert isinstance(next_claim, Claim)
    first.finish(HttpResponse(b"late"))
    assert acquire("abc", "new", database="default", ttl=60).status_code == 409
    next_claim.finish(HttpResponse(b"new"))
    assert acquire("abc", "new", database="default", ttl=60).content == b"new"


async def test_cancelled_handler_preserves_claim_and_event_loop_keeps_running():
    import asyncio

    from asgiref.sync import sync_to_async

    class Cancelled(Controller):
        @post("/", decorators=[idempotent()])
        async def run(self, request):
            raise asyncio.CancelledError

    client = TestAsyncClient(Cancelled.as_router())
    with pytest.raises(asyncio.CancelledError):
        await client.post("/", headers={"Idempotency-Key": "cancel"})
    assert await sync_to_async(IdempotencyRecord.objects.filter(state="running").count)() == 1
    assert (await client.post("/", headers={"Idempotency-Key": "cancel"})).status_code == 409


def test_outer_transaction_is_rejected_before_side_effects():
    from django.core.exceptions import ImproperlyConfigured
    from django.db import transaction

    client = TestClient(PaymentController.as_router())
    with transaction.atomic(), pytest.raises(ImproperlyConfigured, match="autocommit"):
        charge(client, "outer")
    assert User.objects.count() == 0


def test_operation_transaction_does_not_rollback_ownership():
    class Failing(Controller):
        @post("/", atomic=True, decorators=[idempotent()])
        def run(self, request):
            User.objects.create(username="rolled-back")
            raise RuntimeError("fail after a possible external call")

    client = TestClient(Failing.as_router())
    with pytest.raises(RuntimeError):
        client.post("/", headers={"Idempotency-Key": "atomic"})
    assert User.objects.count() == 0
    assert client.post("/", headers={"Idempotency-Key": "atomic"}).status_code == 409
