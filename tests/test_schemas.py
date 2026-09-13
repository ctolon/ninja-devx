from __future__ import annotations

from decimal import Decimal

from ninja import NinjaAPI, Schema
from ninja.testing import TestClient

from ninja_devx import Controller, patch, post
from ninja_devx.serialization.schemas import Input, Output, Patch, PatchData, ReadOnly, WriteOnly


class Price(Schema):
    amount: Decimal
    currency: str = "EUR"


class Account(Schema):
    id: ReadOnly[int]
    email: str
    password: WriteOnly[str]


class Target:
    amount = Decimal(0)
    currency = "USD"


class SchemaController(Controller):
    @patch("/price")
    def change(self, request, payload: Patch[Price]) -> dict[str, object]:
        assert isinstance(payload, PatchData)
        target = payload.apply(Target())
        return {"changed": sorted(payload.changed), "currency": target.currency}

    @post("/accounts", response=Output[Account])
    def create(self, request, payload: Input[Account]) -> dict[str, object]:
        assert payload.id is None  # read-only: ignored even if sent
        return {"id": 7, "email": payload.email, "password": payload.password}


def test_patch_with_postponed_annotations_and_decimals():
    client = TestClient(SchemaController.as_router())
    response = client.patch("/price", json={"currency": "TRY"})
    assert response.json() == {"changed": ["currency"], "currency": "TRY"}


def test_read_only_and_write_only_fields():
    client = TestClient(SchemaController.as_router())
    response = client.post("/accounts", json={"id": 99, "email": "a@b.c", "password": "s3cret"})
    assert response.json() == {"id": 7, "email": "a@b.c"}

    api = NinjaAPI()
    api.add_router("", SchemaController.as_router())
    components = api.get_openapi_schema(path_prefix="")["components"]["schemas"]
    assert set(components["AccountInput"]["properties"]) == {"email", "password"}
    assert set(components["AccountOutput"]["properties"]) == {"id", "email"}
    assert components["PricePatch"]["properties"]["amount"]
