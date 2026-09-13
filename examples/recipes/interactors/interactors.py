from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import QuerySet
from ninja_devx.layers import NotFound

from shop.errors import AlreadyCancelled
from shop.models import Order

from .gateways import OrderGateway


class PlaceOrderInteractor:
    def __init__(self, gateway: OrderGateway, user: User) -> None:
        self.gateway = gateway
        self.user = user

    def __call__(self, product_id: int, quantity: int) -> Order:
        with transaction.atomic():
            product = self.gateway.reserve(product_id, quantity)
            return self.gateway.create(self.user, product, quantity)


class CancelOrderInteractor:
    def __init__(self, gateway: OrderGateway, user: User) -> None:
        self.gateway = gateway
        self.user = user

    def __call__(self, order_id: int) -> Order:
        with transaction.atomic():
            order = self.gateway.owned(self.user).select_for_update().filter(pk=order_id).first()
            if order is None:
                raise NotFound("Order not found")
            if order.status == Order.Status.CANCELLED:
                raise AlreadyCancelled("The order is already cancelled")
            self.gateway.release(order)
            return self.gateway.save_status(order, Order.Status.CANCELLED)


class ListOrdersInteractor:
    def __init__(self, gateway: OrderGateway, user: User) -> None:
        self.gateway = gateway
        self.user = user

    def __call__(self, status: str | None) -> QuerySet[Order]:
        orders = self.gateway.owned(self.user)
        return orders.filter(status=status) if status else orders
