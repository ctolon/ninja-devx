from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja import Query, Schema, Status
from ninja_devx import Controller, ControllerOptions, IsAuthenticated, current_user, get, post
from ninja_devx.layers import NotFound

from shop.models import Order
from shop.schemas import OrderIn, OrderOut

from .selectors import order_list
from .services import order_cancel, order_place


class OrderFilters(Schema):
    status: str | None = None


class OrderController(Controller):
    options = ControllerOptions(permissions=[IsAuthenticated()], tags=["hacksoft"])

    @get("/", response=list[OrderOut])
    def list_orders(self, request: HttpRequest, filters: Query[OrderFilters]) -> list[Order]:
        return list(order_list(user=current_user(request, User), status=filters.status))

    @post("/", response={201: OrderOut})
    def place(self, request: HttpRequest, payload: OrderIn) -> Status[Order]:
        order = order_place(
            user=current_user(request, User),
            product_id=payload.product_id,
            quantity=payload.quantity,
        )
        return Status(201, order)

    @post("/{pk}/cancel", response=OrderOut)
    def cancel(self, request: HttpRequest, pk: int) -> Order:
        order = order_list(user=current_user(request, User)).filter(pk=pk).first()
        if order is None:
            raise NotFound("Order not found")
        return order_cancel(order=order)
