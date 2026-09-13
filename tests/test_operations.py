import inspect

import pytest
from ninja import NinjaAPI, Router

from ninja_devx import Controller, api_operation, delete, get, patch, post, put
from ninja_devx.routing.operations import OperationOptions, get_operation_specs


def test_options_mirror_router_api_operation():
    """Fails when Ninja adds or renames an operation option."""
    ninja_options = {
        name
        for name, parameter in inspect.signature(Router.api_operation).parameters.items()
        if parameter.kind is parameter.KEYWORD_ONLY
    }
    assert (
        set(OperationOptions.__annotations__)
        - {
            "decorators",
            "permissions",
            "hooks",
            "atomic",
            "database",
            "document_errors",
            "errors",
            "raises",
            "meta",
        }
        == ninja_options
    )


def test_decorator_keeps_function_and_records_specs_in_source_order():
    def view(self, request): ...

    decorated = get("/a", summary="A")(post("/b")(view))

    assert decorated is view
    specs = get_operation_specs(view)
    assert [(s.methods, s.path) for s in specs] == [(("GET",), "/a"), (("POST",), "/b")]
    assert specs[0].options == {"summary": "A"}


@pytest.mark.parametrize(
    ("decorator", "method"),
    [(get, "GET"), (post, "POST"), (put, "PUT"), (patch, "PATCH"), (delete, "DELETE")],
)
def test_verb_decorators(client_for, decorator, method):
    class VerbController(Controller):
        @decorator("/")
        def handle(self, request):
            return {"method": request.method}

    response = client_for(VerbController.as_router()).request(method, "/")
    assert response.json() == {"method": method}


def test_api_operation_with_multiple_methods(client_for):
    class MultiController(Controller):
        @api_operation(["get", "post"], "/")
        def handle(self, request):
            return {"method": request.method}

    client = client_for(MultiController.as_router())
    assert client.get("/").json() == {"method": "GET"}
    assert client.post("/").json() == {"method": "POST"}


def test_options_are_passed_to_ninja():
    class DocController(Controller):
        @get(
            "/",
            response={200: list[int]},
            summary="List numbers",
            description="All the numbers.",
            tags=["numbers"],
            deprecated=True,
            openapi_extra={"x-internal": True},
            url_name="numbers",
        )
        def numbers(self, request):
            return [1, 2]

        @get("/hidden", include_in_schema=False, operation_id="custom_id")
        def hidden(self, request): ...

    api = NinjaAPI()
    api.add_router("/numbers", DocController.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]
    operation = paths["/numbers/"]["get"]

    assert operation["summary"] == "List numbers"
    assert operation["description"] == "All the numbers."
    assert operation["tags"] == ["numbers"]
    assert operation["deprecated"] is True
    assert operation["x-internal"] is True
    assert operation["operationId"] == "doc_controller_numbers"
    assert "/numbers/hidden" not in paths
    assert any(p.name == "numbers" for p in api.urls[0])
