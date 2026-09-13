"""Controller in a module using postponed annotations (string annotations)."""

from __future__ import annotations

from ninja import Schema

from ninja_devx import Controller, get, post


class ItemOut(Schema):
    id: int
    name: str


class ItemController(Controller):
    @post("/", response=ItemOut)
    def create(self, request, payload: ItemIn) -> ItemOut:
        return ItemOut(id=1, name=payload.name)

    @get("/{item_id}", response=ItemOut)
    def retrieve(self, request, item_id: int, verbose: bool = False) -> ItemOut:
        return ItemOut(id=item_id, name="verbose" if verbose else "item")


# Defined after the controller on purpose: annotations are only resolved in as_router().
class ItemIn(Schema):
    name: str
