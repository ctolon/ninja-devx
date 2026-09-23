"""Shared model, schemas and payload for the framework overhead workload.

The workload is intentionally HTTP-only (no database): it characterizes the abstraction
each framework adds on top of Django's request handling. Database and N+1 costs are
measured separately by ``benchmarks/load.py`` and ``benchmarks/overhead.py``.
"""

from __future__ import annotations

from ninja import Schema

ITEMS: list[dict[str, object]] = [
    {"id": index, "name": f"Item {index}", "price": index / 10} for index in range(50)
]


class ItemOut(Schema):
    id: int
    name: str
    price: float


class ItemIn(Schema):
    name: str
    price: float
