from django.contrib.auth.models import User
from django.db import transaction
from ninja_devx.layers import NotFound

from shop.errors import AlreadyCancelled, OutOfStock
from shop.models import Order, Product


def order_place(*, user: User, product_id: int, quantity: int) -> Order:
    with transaction.atomic():
        product = Product.objects.select_for_update().filter(pk=product_id).first()
        if product is None:
            raise NotFound("Product not found")
        if product.stock < quantity:
            raise OutOfStock(f"Only {product.stock} left", available=product.stock)
        product.stock -= quantity
        product.save(update_fields=["stock"])
        return Order.objects.create(owner=user, product=product, quantity=quantity)


def order_cancel(*, order: Order) -> Order:
    if order.status == Order.Status.CANCELLED:
        raise AlreadyCancelled("The order is already cancelled")
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=order.product_id)
        product.stock += order.quantity
        product.save(update_fields=["stock"])
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status"])
    return order
