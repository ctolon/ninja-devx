import pytest
from ninja import NinjaAPI, Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, get, post


class EchoController(Controller):
    @get("/")
    def echo(self, request):
        return {"ok": True}

    @get("/async")
    async def aecho(self, request):
        return {"ok": True}


class WhoAmI(Controller):
    @get("/")
    def whoami(self, request):
        return {"user": getattr(request.user, "username", None)}


def test_ninja_client_fixture(ninja_client):
    client = ninja_client(EchoController, auth=None)
    assert isinstance(client, TestClient)
    assert client.get("/").json() == {"ok": True}


def test_clients_as_a_user(ninja_client):
    class Person:
        is_authenticated = True
        username = "ada"

    client = ninja_client(WhoAmI, user=Person())
    assert client.get("/").json() == {"user": "ada"}
    assert ninja_client(EchoController.as_router()).get("/").status_code == 200


async def test_ninja_async_client_fixture(ninja_async_client):
    client = ninja_async_client(EchoController)
    assert isinstance(client, TestAsyncClient)
    assert (await client.get("/async")).json() == {"ok": True}


def test_openapi_snapshot_detects_changes(openapi_snapshot, request):
    api = NinjaAPI(urls_namespace="plugin")
    api.add_router("/echo", EchoController.as_router())
    name = "plugin-self-test"
    path = request.path.parent / "__snapshots__" / request.path.stem / f"{name}.json"
    try:
        openapi_snapshot(api, name)  # first run writes the snapshot
        assert path.exists()
        openapi_snapshot(api, name)  # unchanged: passes
        path.write_text("{}\n")
        with pytest.raises(pytest.fail.Exception, match="differs from"):
            openapi_snapshot(api, name)
    finally:
        path.unlink(missing_ok=True)
        if path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()


def test_update_snapshots_option_is_registered(pytestconfig):
    assert pytestconfig.getoption("--update-snapshots") in (True, False)


class Body(Schema):
    value: int


class Payload(Controller):
    @post("/")
    def create(self, request, payload: Body):
        return payload.model_dump()

    @post("/async")
    async def acreate(self, request, payload: Body):
        return payload.model_dump()


def test_clients_send_json_bodies(ninja_client):
    client = ninja_client(Payload, user=object())
    assert client.post("/", json={"value": 1}).json() == {"value": 1}


async def test_async_clients_send_json_bodies(ninja_async_client):
    client = ninja_async_client(Payload)
    assert (await client.post("/async", json={"value": 2})).json() == {"value": 2}
