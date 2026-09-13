from dataclasses import dataclass
from typing import Annotated

import pytest
from ninja import Header, NinjaAPI, Schema
from ninja.decorators import decorate_view
from ninja.pagination import PageNumberPagination, paginate
from ninja.testing import TestClient

from ninja_devx import (
    Container,
    Controller,
    ControllerConfigError,
    ControllerOptions,
    Scope,
    get,
    post,
)
from tests.future_annotations_app import ItemController


def token_auth(request):
    return request.headers.get("X-Token") == "secret" or None


# --- Instantiation and scopes ------------------------------------------------


class IdentityController(Controller):
    @get("/")
    def identity(self, request):
        return {"id": id(self)}


def test_request_scope_creates_an_instance_per_request():
    client = TestClient(IdentityController.as_router())
    assert client.get("/").json() != client.get("/").json()


def test_singleton_scope_reuses_one_instance_per_router():
    client = TestClient(IdentityController.as_router(scope=Scope.SINGLETON))
    other = TestClient(IdentityController.as_router(scope=Scope.SINGLETON))

    assert client.get("/").json() == client.get("/").json()
    assert client.get("/").json() != other.get("/").json()


def test_singleton_scope_can_be_declared_on_the_class():
    class SingletonController(IdentityController):
        scope = Scope.SINGLETON

    client = TestClient(SingletonController.as_router())
    assert client.get("/").json() == client.get("/").json()


def test_singleton_is_built_eagerly():
    built = []

    class EagerController(Controller):
        scope = Scope.SINGLETON

        def __init__(self) -> None:
            built.append(self)

        @get("/")
        def index(self, request): ...

    EagerController.as_router()
    assert len(built) == 1


@dataclass
class Greeting:
    word: str


class GreetingController(Controller):
    def __init__(self, greeting: Greeting) -> None:
        self.greeting = greeting

    @get("/{name}")
    def greet(self, request, name: str):
        return {"message": f"{self.greeting.word}, {name}"}


def test_dependencies_come_from_the_container():
    container = Container()
    container.instance(Greeting, Greeting("Hello"))

    client = TestClient(GreetingController.as_router(container=container))
    assert client.get("/ada").json() == {"message": "Hello, ada"}


def test_routers_with_different_containers_are_isolated():
    first, second = Container(), Container()
    first.instance(Greeting, Greeting("Hello"))
    second.instance(Greeting, Greeting("Hi"))

    api_one, api_two = NinjaAPI(urls_namespace="one"), NinjaAPI(urls_namespace="two")
    api_one.add_router("/greet", GreetingController.as_router(container=first))
    api_two.add_router("/greet", GreetingController.as_router(container=second))

    assert TestClient(api_one).get("/greet/ada").json() == {"message": "Hello, ada"}
    assert TestClient(api_two).get("/greet/ada").json() == {"message": "Hi, ada"}


def test_same_controller_on_two_prefixes_of_one_api():
    container = Container()
    container.instance(Greeting, Greeting("Hi"))
    api = NinjaAPI(urls_namespace="prefixes")
    api.add_router("/a", GreetingController.as_router(container=container))
    api.add_router("/b", GreetingController.as_router(container=container))

    client = TestClient(api)
    assert client.get("/a/x").status_code == 200
    assert client.get("/b/x").status_code == 200


def test_container_override_applies_to_request_scoped_controllers():
    container = Container()
    container.instance(Greeting, Greeting("Hello"))
    client = TestClient(GreetingController.as_router(container=container))

    with container.override(Greeting, Greeting("Fake")):
        assert client.get("/ada").json() == {"message": "Fake, ada"}
    assert client.get("/ada").json() == {"message": "Hello, ada"}


# --- Ninja features ----------------------------------------------------------


class Filters(Schema):
    tags: list[str] = []


class SearchController(Controller):
    @post("/{category}")
    def search(
        self,
        request,
        category: str,
        payload: Filters,
        trace: Annotated[str, Header(alias="X-Trace")],
        limit: int = 10,
    ):
        return {"category": category, "tags": payload.tags, "trace": trace, "limit": limit}


def test_path_query_body_and_header_parameters():
    client = TestClient(SearchController.as_router())
    response = client.post("/books?limit=3", json={"tags": ["python"]}, headers={"X-Trace": "abc"})
    assert response.json() == {"category": "books", "tags": ["python"], "trace": "abc", "limit": 3}


def test_validation_errors_are_handled_by_ninja():
    client = TestClient(SearchController.as_router())
    assert client.post("/books?limit=x", json={}, headers={"X-Trace": "a"}).status_code == 422


def test_postponed_annotations_are_resolved_against_the_method_module():
    client = TestClient(ItemController.as_router())
    assert client.post("/", json={"name": "pen"}).json() == {"id": 1, "name": "pen"}
    assert client.get("/7?verbose=true").json() == {"id": 7, "name": "verbose"}


def test_class_level_auth_and_route_level_override():
    class SecretController(Controller):
        options = ControllerOptions(auth=token_auth)

        @get("/private")
        def private(self, request):
            return {"ok": True}

        @get("/public", auth=None)
        def public(self, request):
            return {"ok": True}

    client = TestClient(SecretController.as_router())
    assert client.get("/private").status_code == 401
    assert client.get("/private", headers={"X-Token": "secret"}).status_code == 200
    assert client.get("/public").status_code == 200


def test_as_router_arguments_override_class_settings():
    class OpenController(Controller):
        @get("/")
        def index(self, request):
            return {"ok": True}

    client = TestClient(OpenController.as_router(auth=token_auth))
    assert client.get("/").status_code == 401


def test_wrapping_decorators_via_decorators_option():
    class NumberController(Controller):
        @get("/", response=list[int], decorators=[paginate(PageNumberPagination, page_size=2)])
        def numbers(self, request):
            return list(range(5))

    # Built twice: decorators must not leak state into the method between routers.
    NumberController.as_router()
    client = TestClient(NumberController.as_router())
    assert client.get("/?page=2").json() == {"items": [2, 3], "count": 5}


def test_attribute_based_ninja_decorators_stacked_below():
    def add_header(view):
        def wrapper(request, *args, **kwargs):
            response = view(request, *args, **kwargs)
            response["X-Decorated"] = "yes"
            return response

        return wrapper

    class DecoratedController(Controller):
        @get("/")
        @decorate_view(add_header)
        def index(self, request):
            return {"ok": True}

    DecoratedController.as_router()
    response = TestClient(DecoratedController.as_router()).get("/")
    assert response["X-Decorated"] == "yes"


# --- Inheritance -------------------------------------------------------------


class BaseProfileController(Controller):
    @get("/me")
    def me(self, request):
        return {"who": "me"}

    @get("/{user_id}")
    def retrieve(self, request, user_id: str):
        return {"who": user_id}

    @get("/{user_id}/avatar")
    def avatar(self, request, user_id: str):
        return {"avatar": user_id}


class ProfileController(BaseProfileController):
    @get("/{user_id}")
    def retrieve(self, request, user_id: str):
        return {"who": user_id.upper()}

    def avatar(self, request, user_id: str):  # undecorated override removes the route
        return super().avatar(request, user_id)

    @get("/{user_id}/posts")
    def posts(self, request, user_id: str):
        return {"posts": user_id}


def test_inherited_operations_keep_order_and_overrides():
    router = ProfileController.as_router()
    assert list(router.path_operations) == ["/me", "/{user_id}", "/{user_id}/posts"]

    client = TestClient(router)
    assert client.get("/me").json() == {"who": "me"}  # still matched before /{user_id}
    assert client.get("/ada").json() == {"who": "ADA"}
    assert client.get("/ada/posts").json() == {"posts": "ada"}


# --- Configuration errors ----------------------------------------------------


def test_controller_without_operations_is_rejected():
    class EmptyController(Controller):
        def helper(self): ...

    with pytest.raises(ControllerConfigError, match="declares no operations"):
        EmptyController.as_router()


def test_required_constructor_arguments_need_a_container():
    with pytest.raises(ControllerConfigError, match="'greeting'; pass container"):
        GreetingController.as_router()


@pytest.mark.parametrize("wrapper", [staticmethod, classmethod])
def test_operations_must_be_instance_methods(wrapper):
    def view(request): ...

    controller = type("BadController", (Controller,), {"view": wrapper(get("/")(view))})
    with pytest.raises(ControllerConfigError, match="plain instance methods"):
        controller.as_router()


def test_operation_without_self_is_rejected():
    class NoSelfController(Controller):
        @get("/")
        def view(*args): ...

    with pytest.raises(ControllerConfigError, match="must accept `self` and `request`"):
        NoSelfController.as_router()


def test_unresolvable_annotations_are_reported():
    class BrokenController(Controller):
        @get("/")
        def view(self, request, value: "Missing"): ...  # noqa: F821

    with pytest.raises(ControllerConfigError, match=r"BrokenController\.view"):
        BrokenController.as_router()


def test_ninja_registration_errors_mention_the_method():
    class InvalidController(Controller):
        @get("/", response=object())
        def view(self, request): ...

    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - Ninja's own error type
        InvalidController.as_router()
    assert any("InvalidController.view" in note for note in exc_info.value.__notes__)


# --- Options layering --------------------------------------------------------


def add_header(name):
    def decorator(view):
        def wrapper(request, *args, **kwargs):
            response = view(request, *args, **kwargs)
            response[name] = "yes"
            return response

        return wrapper

    return decorator


class TaggedBase(Controller):
    options = ControllerOptions(tags=["base"], auth=token_auth)


class TaggedController(TaggedBase):
    options = ControllerOptions(tags=["child"])

    @get("/")
    def index(self, request):
        return {"ok": True}

    @get("/public", auth=None, tags=["public"])
    def public(self, request):
        return {"ok": True}


def test_options_are_merged_along_the_mro():
    assert TaggedController.merged_options() == {"tags": ["child"], "auth": token_auth}
    client = TestClient(TaggedController.as_router())
    assert client.get("/").status_code == 401
    assert client.get("/public").status_code == 200


def test_as_router_options_override_class_options():
    client = TestClient(TaggedController.as_router(auth=None))
    assert client.get("/").status_code == 200


def test_openapi_tags_follow_the_layers():
    api = NinjaAPI()
    api.add_router("", TaggedController.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]
    assert paths["/"]["get"]["tags"] == ["child"]
    assert paths["/public"]["get"]["tags"] == ["public"]


def test_controller_decorators_wrap_operation_decorators():
    calls = []

    def record(label):
        def decorator(view):
            def wrapper(request, *args, **kwargs):
                calls.append(label)
                return view(request, *args, **kwargs)

            return wrapper

        return decorator

    class DecoratedController(Controller):
        options = ControllerOptions(decorators=[record("controller")])

        @get("/", decorators=[record("operation")])
        def index(self, request):
            return {"ok": True}

    TestClient(DecoratedController.as_router()).get("/")
    assert calls == ["controller", "operation"]


def test_customize_operation_hook():
    class SummaryController(Controller):
        @classmethod
        def customize_operation(cls, name, spec):
            return spec.with_options(summary=f"Custom {name}")

        @get("/")
        def index(self, request): ...

    api = NinjaAPI()
    api.add_router("", SummaryController.as_router())
    assert api.get_openapi_schema(path_prefix="")["paths"]["/"]["get"]["summary"] == "Custom index"
