from ninja import NinjaAPI, Router
from ninja.testing import TestClient

from ninja_devx import Container, Controller, ControllerOptions, Mount, Scope, get, mount


class Greeting:
    def __init__(self) -> None:
        self.word = "hello"


class HelloController(Controller):
    def __init__(self, greeting: Greeting) -> None:
        self.greeting = greeting

    @get("/", url_name="hello")
    def hello(self, request):
        return {"word": self.greeting.word, "id": id(self)}


class PingController(Controller):
    @get("/")
    def ping(self, request):
        return {"ok": True}


def test_mount_many_controllers_with_shared_container():
    api = NinjaAPI(urls_namespace="mounting")
    routers = mount(
        api, {"/hello": HelloController, "/ping": PingController}, container=Container()
    )
    assert set(routers) == {"/hello", "/ping"}
    client = TestClient(api)
    assert client.get("/hello/").json()["word"] == "hello"
    assert client.get("/ping/").json() == {"ok": True}


def test_versioned_mounts():
    api = NinjaAPI(urls_namespace="versions")
    routes = {"/hello": HelloController}
    mount(api, routes, prefix="/v1", container=Container(), deprecated=True)
    mount(api, routes, prefix="/v2", container=Container())

    paths = api.get_openapi_schema(path_prefix="")["paths"]
    assert paths["/v1/hello/"]["get"]["deprecated"] is True
    assert "deprecated" not in paths["/v2/hello/"]["get"]
    client = TestClient(api)
    assert client.get("/v1/hello/").status_code == client.get("/v2/hello/").status_code == 200

    names = {pattern.name for pattern in api.urls[0] if getattr(pattern, "name", None)}
    assert {"v1_hello", "v2_hello"} <= names
    ids = {op["operationId"] for path in paths.values() for op in path.values()}
    assert ids == {"v1_hello_controller_hello", "v2_hello_controller_hello"}


def test_mount_entries_override_shared_settings():
    api = NinjaAPI(urls_namespace="overrides")
    mount(
        api,
        {
            "/singleton": Mount(HelloController, scope=Scope.SINGLETON),
            "/private": Mount(PingController, options=ControllerOptions(auth=lambda request: None)),
        },
        container=Container(),
    )
    client = TestClient(api)
    assert client.get("/singleton/").json()["id"] == client.get("/singleton/").json()["id"]
    assert client.get("/private/").status_code == 401


def test_mount_on_a_router():
    parent = Router()
    mount(parent, {"/ping": PingController}, prefix="/nested")
    api = NinjaAPI(urls_namespace="router-mount")
    api.add_router("/api", parent)
    assert TestClient(api).get("/api/nested/ping/").json() == {"ok": True}
