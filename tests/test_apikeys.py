from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.utils import timezone
from ninja import NinjaAPI
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, ControllerOptions, get, post
from ninja_devx.contrib.apikeys.api import APIKeyController
from ninja_devx.contrib.apikeys.auth import (
    APIKeyAuth,
    APIKeyBearer,
    APIKeyRateThrottle,
    HasScopes,
    RequiresScope,
    create_api_key,
    revoke_api_key,
    rotate_api_key,
    scope_allows,
)
from ninja_devx.contrib.apikeys.models import APIKey
from ninja_devx.testing.clients import assert_max_hops

pytestmark = pytest.mark.django_db


def test_rotate_api_key_replaces_the_secret_and_keeps_the_row():
    owner = User.objects.create(username="rotate-owner")
    key, old_raw = create_api_key(owner, "deploys", scopes=["orders:read"])
    old_prefix = key.prefix
    rotated, new_raw = rotate_api_key(key, scopes=["orders:read", "orders:write"])
    assert rotated.pk == key.pk
    assert rotated.prefix != old_prefix
    assert rotated.scopes == ["orders:read", "orders:write"]
    assert new_raw.startswith("ndx_")
    assert new_raw != old_raw


def test_rotate_refuses_revoked_keys_and_clears_limits():
    owner = User.objects.create(username="rotate-limits")
    key, _ = create_api_key(owner, "limits", rate_limit="10/m")
    rotated, _ = rotate_api_key(key, rate_limit=None)
    assert rotated.rate_limit == ""
    rotated, _ = rotate_api_key(key, rate_limit="5/m")
    assert rotated.rate_limit == "5/m"
    revoke_api_key(key)
    with pytest.raises(ValueError, match="revoked"):
        rotate_api_key(key)
    with pytest.raises(CommandError, match="revoked"):
        call_command("devx_apikey", "rotate", "--prefix", key.prefix)


def test_rotate_command_prints_the_new_key(capsys):
    owner = User.objects.create(username="rotate-cmd")
    key, _ = create_api_key(owner, "cmd")
    call_command("devx_apikey", "rotate", "--prefix", key.prefix)
    assert "Rotated" in capsys.readouterr().out


def test_key_pagination_is_bounded_and_owner_scoped():
    owner = User.objects.create(username="pagination-owner")
    other = User.objects.create(username="pagination-other")
    APIKey.objects.bulk_create(
        [APIKey(user=owner, name=str(i), prefix=f"page-{i}", hashed_secret="x") for i in range(125)]
        + [APIKey(user=other, name="hidden", prefix="other-page", hashed_secret="x")]
    )
    client = TestClient(APIKeyController.as_router())
    first = client.get("/?page_size=10000", user=owner).json()
    assert first["count"] == 125
    assert len(first["items"]) == 100
    second = client.get("/?page=2&page_size=100", user=owner).json()
    assert len(second["items"]) == 25
    assert {item["id"] for item in first["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )
    assert client.get("/?page=0", user=owner).status_code == 422


class Orders(Controller):
    options = ControllerOptions(auth=[APIKeyAuth(), APIKeyBearer()], permissions=[HasScopes()])

    @get("/", meta=(RequiresScope("orders:read"),))
    def list_orders(self, request):
        return {"user": request.auth.username}

    @post("/", meta=(RequiresScope("orders:write"),))
    def create(self, request):
        return {"ok": True}


def test_scope_matching():
    assert scope_allows(["orders:read"], "orders:read")
    assert scope_allows(["orders:*"], "orders:write")
    assert scope_allows(["*"], "anything:else")
    assert not scope_allows(["orders:read"], "orders:write")
    assert not scope_allows(["invoices:*"], "orders:read")


def test_keys_authenticate_and_scope_operations():
    ada = User.objects.create(username="ada")
    key, raw = create_api_key(ada, "reader", scopes=["orders:read"])
    assert key.hashed_secret not in raw
    client = TestClient(Orders.as_router())
    assert client.get("/", headers={"X-API-Key": raw}).json() == {"user": "ada"}
    assert client.get("/", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    assert client.post("/", headers={"X-API-Key": raw}).status_code == 403
    assert client.get("/", headers={"X-API-Key": raw + "x"}).status_code == 401
    assert client.get("/", headers={"X-API-Key": "ndx_nope_nope"}).status_code == 401
    key.refresh_from_db()
    assert key.last_used_at is not None


def test_revoked_expired_and_inactive_keys_fail():
    ada = User.objects.create(username="ada")
    client = TestClient(Orders.as_router())
    revoked, raw_revoked = create_api_key(ada, "old", scopes=["*"])
    revoke_api_key(revoked)
    _, raw_expired = create_api_key(
        ada, "exp", scopes=["*"], expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert client.get("/", headers={"X-API-Key": raw_revoked}).status_code == 401
    assert client.get("/", headers={"X-API-Key": raw_expired}).status_code == 401
    _, raw = create_api_key(ada, "ok", scopes=["*"])
    User.objects.filter(pk=ada.pk).update(is_active=False)
    assert client.get("/", headers={"X-API-Key": raw}).status_code == 401


def test_self_service_key_management():
    ada = User.objects.create(username="ada")
    bob = User.objects.create(username="bob")
    client = TestClient(APIKeyController.as_router())
    created = client.post("/", json={"name": "ci", "scopes": ["orders:read"]}, user=ada)
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("ndx_")
    assert [k["name"] for k in client.get("/", user=ada).json()["items"]] == ["ci"]
    assert "key" not in client.get("/", user=ada).json()["items"][0]
    assert client.delete(f"/{body['id']}", user=bob).status_code == 404
    assert client.delete(f"/{body['id']}", user=ada).status_code == 204
    assert APIKey.objects.get(pk=body["id"]).revoked_at is not None


def test_management_command(capsys):
    User.objects.create(username="ada")
    call_command(
        "devx_apikey",
        "create",
        "--user",
        "ada",
        "--name",
        "deploy",
        "--scope",
        "deploy:*",
        "--days",
        "30",
    )
    raw = capsys.readouterr().out.strip().splitlines()[-1]
    key = APIKey.objects.get()
    assert raw.startswith(f"ndx_{key.prefix}_")
    assert key.scopes == ["deploy:*"]
    call_command("devx_apikey", "revoke", "--prefix", key.prefix)
    key.refresh_from_db()
    assert key.revoked_at is not None


class AsyncOrders(Controller):
    options = ControllerOptions(auth=APIKeyAuth(), permissions=[HasScopes()])

    @get("/", meta=(RequiresScope("orders:read"),))
    async def list_orders(self, request):
        return {"user": request.auth.username}


@pytest.mark.django_db(transaction=True)
async def test_async_operations_authenticate_in_one_thread_hop():
    from asgiref.sync import sync_to_async

    ada = await User.objects.acreate(username="ada")
    _, raw = await sync_to_async(create_api_key)(ada, "reader", scopes=["orders:read"])
    client = TestAsyncClient(AsyncOrders.as_router())
    with assert_max_hops(1):
        response = await client.get("/", headers={"X-API-Key": raw})
    assert response.json() == {"user": "ada"}


def test_openapi_security_scheme():
    api = NinjaAPI(urls_namespace="apikeys")
    api.add_router("/orders", Orders.as_router())
    schemes = api.get_openapi_schema(path_prefix="")["components"]["securitySchemes"]
    assert schemes["APIKeyAuth"] == {"type": "apiKey", "in": "header", "name": "X-API-Key"}


class Limited(Controller):
    options = ControllerOptions(auth=[APIKeyAuth()], throttle=[APIKeyRateThrottle("3/min")])

    @get("/")
    def ping(self, request):
        return {"ok": True}


def test_per_key_rate_limits():
    from django.core.cache import cache
    from django.core.exceptions import ValidationError

    cache.clear()
    ada = User.objects.create(username="ada")
    _, default = create_api_key(ada, "default")
    _, generous = create_api_key(ada, "generous", rate_limit="5/min")
    client = TestClient(Limited.as_router())

    statuses = [client.get("/", headers={"X-API-Key": default}).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    generous_statuses = [
        client.get("/", headers={"X-API-Key": generous}).status_code for _ in range(6)
    ]
    assert generous_statuses == [200] * 5 + [429]  # each key has its own budget
    response = client.get("/", headers={"X-API-Key": generous})
    assert response["RateLimit-Limit"] == "5"

    with pytest.raises(ValidationError):
        create_api_key(ada, "typo", rate_limit="5/fortnight")
