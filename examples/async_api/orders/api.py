import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from django.http import HttpRequest
from ninja import Schema
from ninja.streaming import SSE
from ninja_devx import ControllerOptions, Inject, OperationInfo, get, post
from ninja_devx.crud import CRUDController, Lookup
from ninja_devx.layers import Conflict
from pydantic import Field

from orders.models import Order
from orders.payments import PaymentDeclined, PaymentGateway


class ProductOut(Schema):
    id: int
    name: str
    price: Decimal


class OrderOut(Schema):
    id: int
    product: ProductOut  # joined automatically: no lazy load in the event loop
    quantity: int
    status: Literal["pending", "paid"]
    payment_reference: str
    created: datetime


class OrderIn(Schema):
    product_id: int
    quantity: Annotated[int, Field(ge=1, le=100)]


class OrderEvent(Schema):
    id: int
    status: Literal["pending", "paid"]


class AlreadyPaid(Conflict):
    """The order is already paid."""

    code = "already_paid"


class Timing:
    """An async hook: measures every call, including whole streams."""

    def __init__(self) -> None:
        self.timings: deque[tuple[str, float]] = deque(maxlen=1000)

    @asynccontextmanager
    async def around_async(
        self, request: HttpRequest, operation: OperationInfo, /
    ) -> AsyncGenerator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings.append((operation.operation_id, time.perf_counter() - started))


class OrderController(CRUDController[Order, OrderOut, OrderIn]):
    """Orders run async (``ASYNC_MODE``): CRUD writes take one thread hop each."""

    options = ControllerOptions(hooks=[Timing()], tags=["orders"])
    owner_field = "customer"
    scope_queryset_to_owner = True
    filter_fields = {"status": ("exact",)}
    routes = {"update": {"enabled": False}, "partial_update": {"enabled": False}}

    @post("/{pk}/pay", response=OrderOut, raises=(PaymentDeclined, AlreadyPaid))
    async def pay(self, request: HttpRequest, pk: Lookup, gateway: Inject[PaymentGateway]) -> Order:
        order = await self.aget_object(request, pk)  # one hop: lookup + object permissions
        if order.status == Order.Status.PAID:
            raise AlreadyPaid()
        amount = order.product.price * order.quantity
        reference = await gateway.charge(amount, reference=f"order-{order.pk}")  # awaited, no hop
        return await self.run_atomic(self._mark_paid, request, order.pk, reference)  # one hop

    def _mark_paid(self, request: HttpRequest, pk: int, reference: str) -> Order:
        order = self.get_object(request, pk, lock=True)
        order.status = Order.Status.PAID
        order.payment_reference = reference
        order.save(update_fields=["status", "payment_reference"])
        return self.refresh(request, order)

    @get("/events", response=SSE[OrderEvent])
    async def events(self, request: HttpRequest, limit: int = 20) -> AsyncIterator[OrderEvent]:
        """Server-sent events with the caller's latest orders."""
        await self.aprepare_request(request)
        async for order in self.scoped_queryset(request)[:limit]:
            yield OrderEvent.model_validate({"id": order.pk, "status": order.status})
