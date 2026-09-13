from typing import Protocol

from django.contrib.auth.models import User
from django.db.models import QuerySet
from ninja_devx.layers import NotFound

from shop.errors import OutOfStock
from shop.models import Order, Product


class OrderGateway(Protocol):
    def reserve(self, product_id: int, quantity: int) -> Product: ...
    def release(self, order: Order) -> None: ...
    def create(self, owner: User, product: Product, quantity: int) -> Order: ...
    def owned(self, owner: User) -> QuerySet[Order]: ...
    def save_status(self, order: Order, status: str) -> Order: ...


class DjangoOrderGateway:
    def reserve(self, product_id: int, quantity: int) -> Product:
        product = Product.objects.select_for_update().filter(pk=product_id).first()
        if product is None:
            raise NotFound("Product not found")
        if product.stock < quantity:
            raise OutOfStock(f"Only {product.stock} left", available=product.stock)
        product.stock -= quantity
        product.save(update_fields=["stock"])
        return product

    def release(self, order: Order) -> None:
        product = Product.objects.select_for_update().get(pk=order.product_id)
        product.stock += order.quantity
        product.save(update_fields=["stock"])

    def create(self, owner: User, product: Product, quantity: int) -> Order:
        return Order.objects.create(owner=owner, product=product, quantity=quantity)

    def owned(self, owner: User) -> QuerySet[Order]:
        return Order.objects.filter(owner=owner)

    def save_status(self, order: Order, status: str) -> Order:
        order.status = status
        order.save(update_fields=["status"])
        return order
