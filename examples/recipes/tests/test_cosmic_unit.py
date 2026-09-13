"""Cosmic-lite handlers run without Django's database: fakes for every port."""

import pytest
from django.contrib.auth.models import User
from ninja_devx.layers.testing import InMemoryRepository, make_context

from cosmic.use_cases import CancelOrder, CancelOrderHandler, PlaceOrder, PlaceOrderHandler
from shop.errors import AlreadyCancelled, OutOfStock
from shop.models import Order, Product


@pytest.fixture
def world() -> tuple[InMemoryRepository[Product], InMemoryRepository[Order], Product]:
    products = InMemoryRepository(Product)
    orders = InMemoryRepository(Order)
    lamp = products.add({"name": "Lamp", "stock": 3})
    return products, orders, lamp


def test_place_and_cancel_without_a_database(world):
    products, orders, lamp = world
    context = make_context(User(pk=1, username="ada"), None)
    order = PlaceOrderHandler(products, orders, context)(PlaceOrder(lamp.pk, 2))
    assert (lamp.stock, order.quantity) == (1, 2)

    CancelOrderHandler(products, orders, context)(CancelOrder(order.pk))
    assert (lamp.stock, order.status) == (3, Order.Status.CANCELLED)
    with pytest.raises(AlreadyCancelled):
        CancelOrderHandler(products, orders, context)(CancelOrder(order.pk))


def test_out_of_stock(world):
    products, orders, lamp = world
    context = make_context(User(pk=1, username="ada"), None)
    with pytest.raises(OutOfStock):
        PlaceOrderHandler(products, orders, context)(PlaceOrder(lamp.pk, 9))
    assert orders.items == {}
