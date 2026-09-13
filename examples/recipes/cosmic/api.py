from django.contrib.auth.models import User
from django.db.models import QuerySet
from django.http import HttpRequest
from ninja_devx import (
    Controller,
    ControllerOptions,
    Inject,
    IsAuthenticated,
    current_user,
    get,
    post,
    use_case,
)

from shop.models import Order
from shop.schemas import OrderIn, OrderOut

from .use_cases import CancelOrder, CancelOrderHandler, PlaceOrder, PlaceOrderHandler


def to_place_order(payload: OrderIn) -> PlaceOrder:
    return PlaceOrder(product_id=payload.product_id, quantity=payload.quantity)


class OrderController(Controller):
    options = ControllerOptions(permissions=[IsAuthenticated()], tags=["cosmic"])

    @get("/", response=list[OrderOut])
    def list_orders(self, request: HttpRequest, status: str | None = None) -> QuerySet[Order]:
        orders = Order.objects.filter(owner=current_user(request, User))
        return orders.filter(status=status) if status else orders

    place = use_case(
        post("/", response={201: OrderOut}), PlaceOrderHandler, command=to_place_order, status=201
    )

    @post("/{pk}/cancel", response=OrderOut)
    def cancel(self, request: HttpRequest, pk: int, handler: Inject[CancelOrderHandler]) -> Order:
        return handler(CancelOrder(order_id=pk))
