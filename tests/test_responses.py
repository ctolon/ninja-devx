"""Response headers, cookies and status use Django Ninja's own mechanisms."""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI, Schema, Status
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, get, post


class ItemOut(Schema):
    id: int


class Items(Controller):
    @get(
        "/",
        response=list[ItemOut],
        openapi_extra={
            "responses": {
                200: {  # an int key merges with Ninja's generated 200 response
                    "headers": {"X-Total-Count": {"schema": {"type": "integer"}}},
                }
            }
        },
    )
    def list_items(self, request: HttpRequest, response: HttpResponse) -> list[ItemOut]:
        response["X-Total-Count"] = "2"
        response.set_cookie("seen", "1")
        return [ItemOut(id=1), ItemOut(id=2)]

    @post("/new", response={201: ItemOut})
    async def create(self, request: HttpRequest, response: HttpResponse) -> Status[ItemOut]:
        response["Location"] = "/items/3"
        return Status(201, ItemOut(id=3))


def test_temporal_response_sets_headers_and_cookies():
    response = TestClient(Items.as_router()).get("/")
    assert response.status_code == 200
    assert response["X-Total-Count"] == "2"
    assert response.cookies["seen"].value == "1"


async def test_temporal_response_in_async_operations():
    response = await TestAsyncClient(Items.as_router()).post("/new")
    assert (response.status_code, response["Location"]) == (201, "/items/3")


def test_response_parameter_is_not_documented_as_input():
    api = NinjaAPI(urls_namespace="responses")
    api.add_router("/items", Items.as_router())
    schema = api.get_openapi_schema(path_prefix="")
    operation = schema["paths"]["/items/"]["get"]
    assert operation["parameters"] == []
    documented = operation["responses"][200]
    assert "X-Total-Count" in documented["headers"]
    assert "application/json" in documented["content"]
