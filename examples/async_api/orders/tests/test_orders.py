import json
from decimal import Decimal

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from ninja.testing import TestAsyncClient
from ninja_devx.testing.clients import assert_max_hops

from config.urls import api, container
from orders.api import OrderController, Timing
from orders.models import Order, Product
from orders.payments import FakeGateway, PaymentGateway

# Async tests share the database with the thread that runs ORM calls: use transactions.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client() -> TestAsyncClient:
    return TestAsyncClient(api)


@pytest.fixture
async def ada() -> User:
    return await User.objects.acreate(username="ada")


@pytest.fixture
async def lamp() -> Product:
    return await Product.objects.acreate(name="Lamp", price=Decimal("25.00"))


async def test_create_list_and_retrieve(client: TestAsyncClient, ada: User, lamp: Product) -> None:
    # Two hops: Ninja's session authentication (django_auth), then validation, write and
    # reload together. A token auth with an async `authenticate` removes the first one.
    with assert_max_hops(2) as hops:
        created = await client.post(
            "/orders/", json={"product_id": lamp.pk, "quantity": 2}, user=ada
        )
    assert created.status_code == 201
    assert hops.functions[-1] == "CreateMixin._create"
    order = created.json()
    assert order["product"] == {"id": lamp.pk, "name": "Lamp", "price": "25.00"}

    page = (await client.get("/orders/?status=pending", user=ada)).json()
    assert [item["id"] for item in page] == [order["id"]]
    assert (await client.get(f"/orders/{order['id']}", user=ada)).json()["quantity"] == 2


async def test_pay_uses_the_async_gateway_and_one_atomic_hop(
    client: TestAsyncClient, ada: User, lamp: Product
) -> None:
    order = await Order.objects.acreate(customer=ada, product=lamp, quantity=4)
    fake = FakeGateway()
    with container.override(PaymentGateway, fake):
        paid = await client.post(f"/orders/{order.pk}/pay", user=ada)
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"
    assert fake.charges == [Decimal("100.00")]

    again = await client.post(f"/orders/{order.pk}/pay", user=ada)
    assert (again.status_code, again.json()["code"]) == (409, "already_paid")


async def test_declined_payments_are_402(client: TestAsyncClient, ada: User, lamp: Product) -> None:
    order = await Order.objects.acreate(customer=ada, product=lamp, quantity=100)
    declined = await client.post(f"/orders/{order.pk}/pay", user=ada)
    assert (declined.status_code, declined.json()["code"]) == (402, "payment_declined")
    await order.arefresh_from_db()
    assert order.status == Order.Status.PENDING


async def test_orders_are_private_and_validated(
    client: TestAsyncClient, ada: User, lamp: Product
) -> None:
    bob = await User.objects.acreate(username="bob")
    order = await Order.objects.acreate(customer=ada, product=lamp, quantity=1)
    assert (await client.get(f"/orders/{order.pk}", user=bob)).status_code == 404
    assert (await client.get("/orders/")).status_code == 401
    invalid = await client.post("/orders/", json={"product_id": lamp.pk, "quantity": 0}, user=ada)
    assert invalid.status_code == 422


async def test_server_sent_events(client: TestAsyncClient, ada: User, lamp: Product) -> None:
    first = await Order.objects.acreate(customer=ada, product=lamp, quantity=1)
    second = await Order.objects.acreate(customer=ada, product=lamp, quantity=1)
    timing = next(h for h in OrderController.options["hooks"] if isinstance(h, Timing))
    timing.timings.clear()
    response = await client.get("/orders/events", user=ada)
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.content.decode().splitlines()
        if line.startswith("data: ")
    ]
    assert [event["id"] for event in events] == [second.pk, first.pk]
    assert [name for name, _ in timing.timings] == ["order_controller_events"]


async def test_checks_pass() -> None:
    from django.core.management import call_command

    await sync_to_async(call_command)("check", fail_level="WARNING")
