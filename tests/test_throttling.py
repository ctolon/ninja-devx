import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from ninja.testing import TestClient

from ninja_devx import Controller, get
from ninja_devx.http.throttling import (
    AnonRateThrottle,
    ClientRateThrottle,
    ScopedRateThrottle,
    TenantRateThrottle,
    UserRateThrottle,
    parse_rate,
)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def test_parse_rate():
    assert parse_rate("100/min") == (100, 60)
    assert parse_rate("20/5min") == (20, 300)
    assert parse_rate("3/day") == (3, 86400)
    with pytest.raises(ImproperlyConfigured):
        parse_rate("often")


class Limited(Controller):
    @get("/user", throttle=[UserRateThrottle("2/min")])
    def user(self, request):
        return {"ok": True}

    @get("/anon", throttle=[AnonRateThrottle("1/min")])
    def anon(self, request):
        return {"ok": True}

    @get("/client", throttle=[ClientRateThrottle(user="3/min", anon="1/min")])
    def client(self, request):
        return {"ok": True}

    @get("/upload-a", throttle=[ScopedRateThrottle("uploads")])
    def upload_a(self, request):
        return {"ok": True}

    @get("/upload-b", throttle=[ScopedRateThrottle("uploads")])
    def upload_b(self, request):
        return {"ok": True}

    @get("/tenant", throttle=[TenantRateThrottle("1/min")])
    def tenant(self, request):
        return {"ok": True}


@pytest.mark.django_db
def test_user_throttle_counts_per_user_and_sets_retry_after():
    ada, bob = User.objects.create(username="ada"), User.objects.create(username="bob")
    client = TestClient(Limited.as_router())
    assert [client.get("/user", user=ada).status_code for _ in range(3)] == [200, 200, 429]
    assert int(client.get("/user", user=ada)["Retry-After"]) >= 1
    assert client.get("/user", user=bob).status_code == 200
    assert client.get("/user").status_code == 200  # anonymous: not this throttle's business


@pytest.mark.django_db
def test_anon_and_client_throttles():
    ada = User.objects.create(username="ada")
    client = TestClient(Limited.as_router())
    assert [client.get("/anon").status_code for _ in range(2)] == [200, 429]
    assert client.get("/anon", user=ada).status_code == 200
    assert [client.get("/client").status_code for _ in range(2)] == [200, 429]
    assert [client.get("/client", user=ada).status_code for _ in range(4)] == [200, 200, 200, 429]


@pytest.mark.django_db
def test_scoped_throttle_shares_a_budget_and_reads_settings():
    client = TestClient(Limited.as_router())
    with override_settings(NINJA_DEVX={"THROTTLE_RATES": {"uploads": "2/min"}}):
        codes = [client.get(path).status_code for path in ("/upload-a", "/upload-b", "/upload-a")]
    assert codes == [200, 200, 429]
    with pytest.raises(ImproperlyConfigured, match="uploads"):
        client.get("/upload-a")


def test_tenant_throttle():
    client = TestClient(Limited.as_router())

    class Org:
        pk = 7

    assert [client.get("/tenant", tenant=Org()).status_code for _ in range(2)] == [200, 429]
    assert client.get("/tenant").status_code == 200  # no tenant, not throttled
