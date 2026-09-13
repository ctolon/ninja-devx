from ninja import NinjaAPI, Schema

from ninja_devx import Controller, get, post
from tests.future_annotations_app import ItemController


class UserIn(Schema):
    name: str


class UserOut(Schema):
    id: int
    name: str


class UserController(Controller):
    @get("/", response=list[UserOut])
    def list(self, request):
        """List all users."""
        return []

    @post("/", response={201: UserOut})
    def create(self, request, payload: UserIn): ...


class GroupController(Controller):
    @get("/")
    def list(self, request): ...


def build_schema():
    api = NinjaAPI()
    api.add_router("/users", UserController.as_router())
    api.add_router("/groups", GroupController.as_router())
    api.add_router("/items", ItemController.as_router())
    return api.get_openapi_schema(path_prefix="")


def test_self_is_not_exposed_and_body_is_detected():
    schema = build_schema()
    create = schema["paths"]["/users/"]["post"]

    assert create["parameters"] == []
    body_ref = create["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert body_ref.endswith("/UserIn")
    assert create["responses"][201]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/UserOut"
    )


def test_docstring_and_method_name_are_used_like_function_views():
    operation = build_schema()["paths"]["/users/"]["get"]
    assert operation["summary"] == "List"
    assert operation["description"] == "List all users."


def test_operation_ids_are_unique_across_controllers():
    schema = build_schema()
    ids = [
        operation["operationId"] for path in schema["paths"].values() for operation in path.values()
    ]
    assert len(ids) == len(set(ids))
    assert {"user_controller_list", "group_controller_list"} <= set(ids)


def test_postponed_annotations_produce_the_same_schema():
    retrieve = build_schema()["paths"]["/items/{item_id}"]["get"]
    params = {p["name"]: p["in"] for p in retrieve["parameters"]}
    assert params == {"item_id": "path", "verbose": "query"}


def test_stacked_operations_get_distinct_ids():
    class AliasController(Controller):
        @get("/current")
        @get("/me")
        def me(self, request): ...

    api = NinjaAPI()
    api.add_router("", AliasController.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]
    assert paths["/current"]["get"]["operationId"] == "alias_controller_me"
    assert paths["/me"]["get"]["operationId"] == "alias_controller_me_2"
