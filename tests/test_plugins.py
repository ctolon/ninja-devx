from ninja import NinjaAPI
from ninja.testing import TestClient

from ninja_devx.http.errors import ErrorMap
from ninja_devx.http.security import SecurityHeadersMiddleware
from ninja_devx.plugins import APIPlugin, install


class Boom(Exception):
    pass


def test_api_plugin_installs_middleware_errors_and_setup():
    events: list[str] = []

    class Ping(APIPlugin):
        def middleware(self):
            return [SecurityHeadersMiddleware()]

        def errors(self):
            return ErrorMap().map(Boom, 418, code="boom")

        def setup(self, api):
            events.append("setup")

    api = NinjaAPI()
    install(api, [Ping()])

    @api.get("/ping")
    def ping(request):
        return {"ok": True}

    @api.get("/boom")
    def boom(request):
        raise Boom

    client = TestClient(api)
    assert events == ["setup"]
    assert client.get("/ping")["X-Content-Type-Options"] == "nosniff"
    assert client.get("/boom").status_code == 418


def test_later_plugins_override_earlier_error_rules():
    class First(APIPlugin):
        def errors(self):
            return ErrorMap().map(Boom, 400, code="boom")

    class Second(APIPlugin):
        def errors(self):
            return ErrorMap().map(Boom, 422, code="boom")

    api = NinjaAPI()
    install(api, [First(), Second()])

    @api.get("/boom")
    def boom(request):
        raise Boom

    assert TestClient(api).get("/boom").status_code == 422


def test_api_plugin_defaults_and_bare_install():
    plugin = APIPlugin()
    assert plugin.middleware() == ()
    assert plugin.errors() is None

    class Bare(APIPlugin):
        done = False

        def setup(self, api):
            self.done = True

    api = NinjaAPI()
    bare = Bare()
    install(api, [])
    install(api, [bare])
    assert bare.done is True
