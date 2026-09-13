from django.contrib.auth.models import User
from django.db.models import QuerySet

from shop.models import Order


def order_list(*, user: User, status: str | None = None) -> QuerySet[Order]:
    orders = Order.objects.filter(owner=user)
    return orders.filter(status=status) if status else orders
