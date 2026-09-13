"""One contract, three architectures: every recipe must behave the same over HTTP."""

import pytest
from django.contrib.auth.models import User
from ninja.testing import TestClient

from config.urls import api
from shop.models import Order, Product

pytestmark = pytest.mark.django_db

RECIPES = ["/hacksoft/orders", "/cosmic/orders", "/dishka/orders"]


@pytest.fixture
def client() -> TestClient:
    return TestClient(api)


@pytest.fixture
def ada() -> User:
    return User.objects.create(username="ada")


@pytest.fixture
def lamp() -> Product:
    return Product.objects.create(name="Lamp", stock=3)


@pytest.mark.parametrize("base", RECIPES)
def test_place_order_reserves_stock(client, base, ada, lamp):
    response = client.post(f"{base}/", json={"product_id": lamp.pk, "quantity": 2}, user=ada)
    assert response.status_code == 201
    assert response.json()["status"] == "placed"
    lamp.refresh_from_db()
    assert lamp.stock == 1


@pytest.mark.parametrize("base", RECIPES)
def test_out_of_stock_is_a_conflict(client, base, ada, lamp):
    response = client.post(f"{base}/", json={"product_id": lamp.pk, "quantity": 5}, user=ada)
    assert response.status_code == 409
    assert response.json() == {"detail": "Only 3 left", "code": "out_of_stock", "available": 3}
    assert Order.objects.count() == 0


@pytest.mark.parametrize("base", RECIPES)
def test_unknown_product_is_not_found(client, base, ada):
    response = client.post(f"{base}/", json={"product_id": 999, "quantity": 1}, user=ada)
    assert (response.status_code, response.json()["code"]) == (404, "not_found")


@pytest.mark.parametrize("base", RECIPES)
def test_cancel_releases_stock_once(client, base, ada, lamp):
    order = client.post(f"{base}/", json={"product_id": lamp.pk, "quantity": 2}, user=ada).json()
    cancelled = client.post(f"{base}/{order['id']}/cancel", user=ada)
    assert cancelled.json()["status"] == "cancelled"
    lamp.refresh_from_db()
    assert lamp.stock == 3
    again = client.post(f"{base}/{order['id']}/cancel", user=ada)
    assert (again.status_code, again.json()["code"]) == (409, "already_cancelled")


@pytest.mark.parametrize("base", RECIPES)
def test_users_only_see_and_cancel_their_orders(client, base, ada, lamp):
    bob = User.objects.create(username="bob")
    order = Order.objects.create(owner=ada, product=lamp, quantity=1)
    assert [o["id"] for o in client.get(f"{base}/", user=ada).json()] == [order.pk]
    assert client.get(f"{base}/", user=bob).json() == []
    assert client.get(f"{base}/?status=cancelled", user=ada).json() == []
    assert client.post(f"{base}/{order.pk}/cancel", user=bob).status_code == 404


@pytest.mark.parametrize("base", RECIPES)
def test_anonymous_requests_are_rejected(client, base):
    assert client.get(f"{base}/").status_code == 401


def test_system_checks_pass():
    from django.core.management import call_command

    call_command("check", fail_level="WARNING")
    call_command("devx_scaffold", "--check")
