import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from ninja_devx import Controller, get, mount
from ninja_devx.contrib.otel import OpenTelemetryMetricsMiddleware
from ninja_devx.http.health import CacheCheck, DatabaseCheck, HealthController, MigrationsCheck
from ninja_devx.http.middleware import use_middleware
from ninja_devx.serialization.renderers import MsgspecRenderer, ORJSONRenderer


class Broken:
    name = "queue"

    def check(self) -> None:
        raise ConnectionError("broker unreachable")


@pytest.mark.django_db(transaction=True)
def test_liveness_and_readiness():
    class Health(HealthController):
        health_checks = (DatabaseCheck(), CacheCheck(), MigrationsCheck())

    client = TestClient(Health.as_router())
    assert client.get("/live").json() == {"status": "ok", "checks": []}
    ready = client.get("/ready").json()
    assert ready["status"] == "ok"
    assert [check["name"] for check in ready["checks"]] == ["database", "cache", "migrations"]

    class Unhealthy(HealthController):
        health_checks = (DatabaseCheck(), Broken())

    response = TestClient(Unhealthy.as_router()).get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"][1] == {
        "name": "queue",
        "status": "error",
        "duration_ms": response.json()["checks"][1]["duration_ms"],
        "error": "dependency_unavailable",
    }


def test_otel_http_server_metrics():
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")

    class Items(Controller):
        @get("/{item_id}")
        def item(self, request, item_id: int):
            return {"id": item_id}

    api = NinjaAPI(urls_namespace="otel")
    use_middleware(api, OpenTelemetryMetricsMiddleware(meter))
    mount(api, {"/items": Items})
    client = TestClient(api)
    client.get("/items/1")
    client.get("/items/nope")
    data = reader.get_metrics_data()
    metrics = {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    points = metrics["http.server.request.duration"].data.data_points
    statuses = sorted(point.attributes["http.response.status_code"] for point in points)
    assert statuses == [200, 422]
    assert all(point.attributes["http.request.method"] == "GET" for point in points)


class Payload(Schema):
    when: datetime
    price: Decimal
    ref: UUID


@pytest.mark.parametrize("renderer", [ORJSONRenderer, MsgspecRenderer])
def test_fast_renderers_match_ninja_json(renderer):
    def make_api(**kwargs):
        api = NinjaAPI(urls_namespace=f"render-{renderer.__name__}-{len(kwargs)}", **kwargs)

        @api.get("/", response=Payload)
        def view(request: HttpRequest):
            return Payload(
                when=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                price=Decimal("9.90"),
                ref=UUID(int=1),
            )

        return api

    fast = TestClient(make_api(renderer=renderer())).get("/")
    default = TestClient(make_api()).get("/")
    assert json.loads(fast.content) == json.loads(default.content)
    assert fast["Content-Type"].startswith("application/json")


def test_readiness_deadline_and_repeated_probes_keep_worker_count_bounded():
    import time
    from threading import Event

    release = Event()
    calls = []

    class Hung:
        name = "hung"

        def check(self):
            calls.append(1)
            release.wait(5)

    class Health(HealthController):
        health_timeout = 0.02
        health_checks = (Hung(),)

    client = TestClient(Health.as_router())
    started = time.perf_counter()
    try:
        for _ in range(3):
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json()["checks"][0]["error"] == "dependency_timeout"
        assert len(calls) == 1
        assert time.perf_counter() - started < 0.5
        assert client.get("/live").status_code == 200
    finally:
        release.set()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_otel_exception_cleanup_balances_active_requests_and_bounds_route_labels(cancelled):
    import asyncio

    from django.test import RequestFactory

    from ninja_devx.http.middleware import middleware_decorator

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = OpenTelemetryMetricsMiddleware(provider.get_meter("cleanup"))

    async def fails(request):
        if cancelled:
            raise asyncio.CancelledError
        raise ValueError("failure")

    wrapped = middleware_decorator(metrics)(fails)
    for pk in range(5):
        with pytest.raises(asyncio.CancelledError if cancelled else ValueError):
            await wrapped(RequestFactory().get(f"/objects/{pk}"))
    data = reader.get_metrics_data()
    collected = {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    active = collected["http.server.active_requests"].data.data_points
    assert all(point.value == 0 for point in active)
    durations = collected["http.server.request.duration"].data.data_points
    assert len(durations) == 1
    assert durations[0].count == 5
    assert durations[0].attributes["http.route"] == "<unmatched>"
    provider.shutdown()


def test_metrics_nested_installation_of_same_instance_balances_counters():
    from django.http import HttpResponse
    from django.test import RequestFactory

    from ninja_devx.http.middleware import middleware_decorator

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = OpenTelemetryMetricsMiddleware(provider.get_meter("nested"))
    response = middleware_decorator(metrics, metrics)(lambda request: HttpResponse())(
        RequestFactory().get("/1")
    )
    assert response.status_code == 200
    data = reader.get_metrics_data()
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == "http.server.active_requests":
                    assert all(point.value == 0 for point in metric.data.data_points)
    provider.shutdown()
