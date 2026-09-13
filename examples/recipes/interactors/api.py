from django.http import HttpRequest
from ninja import Status
from ninja_devx import Controller, ControllerOptions, Inject, IsAuthenticated, get, post

from shop.models import Order
from shop.schemas import OrderIn, OrderOut

from .interactors import CancelOrderInteractor, ListOrdersInteractor, PlaceOrderInteractor


class OrderController(Controller):
    """The list interactor comes through ``__init__``; the others per operation."""

    options = ControllerOptions(permissions=[IsAuthenticated()], tags=["dishka"])

    def __init__(self, list_orders: ListOrdersInteractor) -> None:
        self.list_orders_interactor = list_orders

    @get("/", response=list[OrderOut])
    def list_orders(self, request: HttpRequest, status: str | None = None) -> list[Order]:
        return list(self.list_orders_interactor(status))

    @post("/", response={201: OrderOut})
    def place(
        self, request: HttpRequest, payload: OrderIn, place: Inject[PlaceOrderInteractor]
    ) -> Status[Order]:
        return Status(201, place(payload.product_id, payload.quantity))

    @post("/{pk}/cancel", response=OrderOut)
    def cancel(self, request: HttpRequest, pk: int, cancel: Inject[CancelOrderInteractor]) -> Order:
        return cancel(pk)
