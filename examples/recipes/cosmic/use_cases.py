from dataclasses import dataclass

from django.contrib.auth.models import User
from ninja_devx.layers import NotFound, Repository, RequestContext

from shop.errors import AlreadyCancelled, OutOfStock
from shop.models import Order, Product

Context = RequestContext[User, None]


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    product_id: int
    quantity: int


@dataclass(frozen=True, slots=True)
class CancelOrder:
    order_id: int


class PlaceOrderHandler:
    """Reserve stock and place an order."""

    def __init__(
        self, products: Repository[Product], orders: Repository[Order], context: Context
    ) -> None:
        self.products = products
        self.orders = orders
        self.context = context

    def __call__(self, command: PlaceOrder) -> Order:
        with self.orders.transaction():
            product = self.products.get(command.product_id)
            if product.stock < command.quantity:
                raise OutOfStock(f"Only {product.stock} left", available=product.stock)
            self.products.change(product, {"stock": product.stock - command.quantity})
            return self.orders.add(
                {"owner": self.context.user, "product": product, "quantity": command.quantity}
            )


class CancelOrderHandler:
    """Cancel one of the current user's orders and release its stock."""

    def __init__(
        self, products: Repository[Product], orders: Repository[Order], context: Context
    ) -> None:
        self.products = products
        self.orders = orders
        self.context = context

    def __call__(self, command: CancelOrder) -> Order:
        with self.orders.transaction():
            order = self.orders.get(command.order_id)
            if order.owner_id != self.context.user.pk:
                raise NotFound("Order not found")
            if order.status == Order.Status.CANCELLED:
                raise AlreadyCancelled("The order is already cancelled")
            product = self.products.get(order.product_id)
            self.products.change(product, {"stock": product.stock + order.quantity})
            return self.orders.change(order, {"status": Order.Status.CANCELLED})
