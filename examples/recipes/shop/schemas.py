from typing import Literal

from ninja import Schema


class OrderIn(Schema):
    product_id: int
    quantity: int


class OrderOut(Schema):
    id: int
    product_id: int
    quantity: int
    status: Literal["placed", "cancelled"]
