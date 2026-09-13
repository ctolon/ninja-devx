"""An async payment gateway: a real one would wrap an HTTP client (httpx.AsyncClient)."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Protocol

from ninja_devx.layers import DomainError


class PaymentDeclined(DomainError):
    """The payment was declined."""

    http_status = 402
    code = "payment_declined"


class PaymentGateway(Protocol):
    async def charge(self, amount: Decimal, reference: str) -> str: ...


class FakeGateway:
    """Declines amounts above 1000; stands in for a network client."""

    def __init__(self) -> None:
        self.closed = False
        self.charges: list[Decimal] = []

    async def charge(self, amount: Decimal, reference: str) -> str:
        if amount > 1000:
            raise PaymentDeclined(f"{amount} exceeds the card limit", reference=reference)
        self.charges.append(amount)
        return uuid.uuid4().hex

    async def aclose(self) -> None:
        self.closed = True


async def gateway() -> AsyncIterator[PaymentGateway]:
    """A request-scoped async factory: the client is closed when the request ends."""
    client = FakeGateway()
    try:
        yield client
    finally:
        await client.aclose()
