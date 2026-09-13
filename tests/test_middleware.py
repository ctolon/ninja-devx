from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from ninja import NinjaAPI, Router
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, ControllerOptions, get, mount
from ninja_devx.http.middleware import (
    DeprecationMiddleware,
    Middleware,
    RateLimitHeadersMiddleware,
    RequestIDMiddleware,
    ServerTimingMiddleware,
    use_middleware,
)
from ninja_devx.http.throttling import UserRateThrottle


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


class Recorder(Middleware):
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def process_request(self, request: HttpRequest) -> HttpResponseBase | None:
        self.events.append(f"{self.name}:request")
        return HttpResponse(status=418) if request.headers.get("X-Teapot") else None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        self.events.append(f"{self.name}:response")
        return response


def test_middleware_on_plain_function_views_and_order():
    events: list[str] = []
    router = Router()

    @router.get("/ping")
    def ping(request):
        events.append("view")
        return {"ok": True}

    use_middleware(router, Recorder("outer", events), Recorder("inner", events))
    client = TestClient(router)
    assert client.get("/ping").status_code == 200
    assert events == ["outer:request", "inner:request", "view", "inner:response", "outer:response"]

    events.clear()
    assert client.get("/ping", headers={"X-Teapot": "1"}).status_code == 418
    assert events == ["outer:request", "outer:response"]  # short-circuited before inner and view


class Things(Controller):
    options = ControllerOptions(middleware=[RequestIDMiddleware(), ServerTimingMiddleware()])

    @get("/")
    def things(self, request):
        return {"request_id": request.headers["X-Request-ID"]}

    @get("/async")
    async def athings(self, request):
        return {"request_id": request.headers["X-Request-ID"]}


def test_request_id_and_server_timing():
    client = TestClient(Things.as_router(allow_mixed_path=True))
    given = client.get("/", headers={"X-Request-ID": "abc"})
    assert (given["X-Request-ID"], given.json()["request_id"]) == ("abc", "abc")
    generated = client.get("/")
    assert generated["X-Request-ID"] == generated.json()["request_id"]
    assert generated["Server-Timing"].startswith("app;dur=")


async def test_async_operations_run_async_middleware():
    response = await TestAsyncClient(Things.as_router(allow_mixed_path=True)).get("/async")
    assert response["X-Request-ID"] == response.json()["request_id"]


class Plain(Controller):
    @get("/")
    def plain(self, request):
        return {}


def test_deprecated_versions_send_headers():
    api = NinjaAPI(urls_namespace="deprecation")
    sunset = datetime(2027, 1, 1, tzinfo=UTC)
    mount(
        api,
        {"/things": Plain},
        prefix="/v1",
        deprecated=True,
        middleware=[DeprecationMiddleware(sunset=sunset, link="https://example.com/v2")],
    )
    response = TestClient(api).get("/v1/things/")
    assert response["Deprecation"] == "true"
    assert response["Sunset"] == "Fri, 01 Jan 2027 00:00:00 GMT"
    assert response["Link"] == '<https://example.com/v2>; rel="deprecation"'


class Limited(Controller):
    @get("/", throttle=[UserRateThrottle("2/min")])
    def limited(self, request):
        return {"ok": True}


@pytest.mark.django_db
def test_rate_limit_headers_are_added_for_devx_throttles():
    ada = User.objects.create(username="ada")
    client = TestClient(Limited.as_router())
    first = client.get("/", user=ada)
    assert (first["RateLimit-Limit"], first["RateLimit-Remaining"]) == ("2", "1")
    assert first["RateLimit-Policy"] == '"user";q=2;w=60'
    client.get("/", user=ada)
    rejected = client.get("/", user=ada)
    assert rejected.status_code == 429
    assert rejected["RateLimit-Remaining"] == "0"
    assert int(rejected["RateLimit-Reset"]) >= 1


def test_rate_limit_headers_middleware_does_nothing_without_throttles():
    router = Router()

    @router.get("/")
    def free(request):
        return {}

    use_middleware(router, RateLimitHeadersMiddleware())
    assert not TestClient(router).get("/").has_header("RateLimit-Limit")


@pytest.mark.parametrize("failure_stage", ["request", "response"])
def test_middleware_exception_cleanup_unwinds_all_entered_hooks(failure_stage):
    from django.http import HttpResponse
    from django.test import RequestFactory

    from ninja_devx.http.middleware import Middleware, middleware_decorator

    events = []

    class Outer(Middleware):
        def process_exception(self, request, exception):
            events.append("outer-cleanup")

    class Inner(Middleware):
        def process_request(self, request):
            if failure_stage == "request":
                raise ValueError("original")

        def process_response(self, request, response):
            raise ValueError("original")

        def process_exception(self, request, exception):
            events.append("inner-cleanup")
            raise RuntimeError("cleanup failure must not replace original")

    run = middleware_decorator(Outer(), Inner())(lambda request: HttpResponse())
    with pytest.raises(ValueError, match="original"):
        run(RequestFactory().get("/"))
    assert events == ["inner-cleanup", "outer-cleanup"]
