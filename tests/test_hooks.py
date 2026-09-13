import logging
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja import NinjaAPI
from ninja.testing import TestAsyncClient, TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from ninja_devx import (
    BasePermission,
    Controller,
    ControllerConfigError,
    ControllerOptions,
    DenyAll,
    LoggingHook,
    OperationInfo,
    get,
    get_operation,
    post,
)
from ninja_devx.contrib.otel import OpenTelemetryHook


def test_operation_info_is_available_to_permissions():
    seen: list[OperationInfo | None] = []

    class Spy(BasePermission[object]):
        def has_permission(self, request: HttpRequest, /) -> bool:
            seen.append(get_operation(request))
            return True

    class InfoController(Controller):
        @get("/items", permissions=[Spy()])
        def list_items(self, request):
            operation = get_operation(request)
            assert operation is not None
            return {"id": operation.operation_id, "qualname": operation.qualname}

    response = TestClient(InfoController.as_router()).get("/items")
    body = response.json()
    assert body["id"] == "info_controller_list_items"
    assert body["qualname"].endswith("<locals>.InfoController.list_items")
    assert seen[0] is not None
    assert (seen[0].http_methods, seen[0].path, seen[0].is_async) == (("GET",), "/items", False)


def test_logging_hook_records_outcome_and_duration(caplog):
    class LoggedController(Controller):
        options = ControllerOptions(hooks=[LoggingHook()])

        @get("/ok")
        def ok(self, request):
            return {}

        @get("/denied", permissions=[DenyAll()])
        def denied(self, request): ...

    client = TestClient(LoggedController.as_router())
    with caplog.at_level(logging.INFO, logger="ninja_devx"):
        client.get("/ok")
        client.get("/denied")

    ok, denied = caplog.records
    assert (ok.operation_id, ok.outcome) == ("logged_controller_ok", "ok")  # type: ignore[attr-defined]
    assert denied.outcome == "HttpError"  # type: ignore[attr-defined]
    assert denied.duration_ms >= 0  # type: ignore[attr-defined]


def test_controller_hooks_wrap_operation_hooks():
    events: list[str] = []

    class Recorder:
        def __init__(self, label: str) -> None:
            self.label = label

        @contextmanager
        def around(self, request: HttpRequest, operation: OperationInfo, /) -> Generator[None]:
            events.append(f"{self.label}:start")
            try:
                yield
            except ValueError:
                events.append(f"{self.label}:error")
                raise
            finally:
                events.append(f"{self.label}:end")

    class HookedController(Controller):
        options = ControllerOptions(hooks=[Recorder("controller")])

        @get("/", hooks=[Recorder("operation")])
        def index(self, request):
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        TestClient(HookedController.as_router()).get("/")
    assert events == [
        "controller:start",
        "operation:start",
        "operation:error",
        "operation:end",
        "controller:error",
        "controller:end",
    ]


class LifecycleController(Controller):
    def before_operation(self, request: HttpRequest, operation: OperationInfo) -> None:
        request.META["X_BEFORE"] = operation.method_name

    def after_operation(
        self, request: HttpRequest, operation: OperationInfo, result: object
    ) -> object:
        assert isinstance(result, dict)
        return {**result, "before": request.META["X_BEFORE"]}

    @get("/")
    def index(self, request):
        return {"value": 1}


def test_before_and_after_operation():
    assert TestClient(LifecycleController.as_router()).get("/").json() == {
        "value": 1,
        "before": "index",
    }


async def test_async_lifecycle_methods():
    class AsyncLifecycle(Controller):
        async def before_operation(self, request: HttpRequest, operation: OperationInfo) -> None:
            request.META["X_BEFORE"] = "async"

        async def after_operation(
            self, request: HttpRequest, operation: OperationInfo, result: object
        ) -> object:
            return {"before": request.META["X_BEFORE"], "result": result}

        @get("/")
        async def index(self, request):
            return 1

    response = await TestAsyncClient(AsyncLifecycle.as_router()).get("/")
    assert response.json() == {"before": "async", "result": 1}


def test_async_lifecycle_methods_need_async_operations():
    class Mismatch(Controller):
        async def before_operation(
            self, request: HttpRequest, operation: OperationInfo
        ) -> None: ...

        @get("/")
        def index(self, request): ...

    with pytest.raises(ControllerConfigError, match="before_operation is async"):
        Mismatch.as_router()


@pytest.mark.django_db
def test_atomic_operations_roll_back_on_error():
    class AtomicController(Controller):
        @post("/", atomic=True)
        def create(self, request):
            User.objects.create(username="rolled-back")
            raise RuntimeError("after write")

    with pytest.raises(RuntimeError):
        TestClient(AtomicController.as_router()).post("/")
    assert not User.objects.filter(username="rolled-back").exists()


def test_atomic_requires_sync_operations():
    class AsyncAtomic(Controller):
        options = ControllerOptions(atomic=True)

        @post("/")
        async def create(self, request): ...

    with pytest.raises(ControllerConfigError, match="atomic=True needs a sync operation"):
        AsyncAtomic.as_router()


def test_open_telemetry_hook_records_spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("tests")

    class TracedController(Controller):
        options = ControllerOptions(hooks=[OpenTelemetryHook(tracer)])

        @get("/items/{item_id}")
        def retrieve(self, request, item_id: int):
            return {"id": item_id}

        @get("/fail")
        def fail(self, request):
            raise RuntimeError("nope")

    client = TestClient(TracedController.as_router())
    client.get("/items/3")
    with pytest.raises(RuntimeError):
        client.get("/fail")

    ok, failed = exporter.get_finished_spans()
    assert ok.name == "GET /items/{item_id}"
    assert ok.attributes is not None
    assert ok.attributes["ninja_devx.operation_id"] == "traced_controller_retrieve"
    assert failed.status.status_code is StatusCode.ERROR


def test_deprecated_and_error_documentation_options():
    class DocumentedController(Controller):
        options = ControllerOptions(deprecated=True, auth=lambda request: None)

        @get("/{item_id}", permissions=[DenyAll()])
        def retrieve(self, request, item_id: int): ...

        @get("/quiet", document_errors=False)
        def quiet(self, request, value: int): ...

        @get("/own", response={200: dict[str, int], 401: dict[str, str]}, deprecated=False)
        def own(self, request): ...

    api = NinjaAPI()
    api.add_router("", DocumentedController.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]

    retrieve = paths["/{item_id}"]["get"]
    assert retrieve["deprecated"] is True
    assert sorted(retrieve["responses"]) == [200, 401, 403, 422]
    assert retrieve["responses"][403]["content"]["application/json"]["schema"]["required"] == [
        "detail"
    ]
    assert sorted(paths["/quiet"]["get"]["responses"]) == [200]
    own = paths["/own"]["get"]
    assert "deprecated" not in own
    declared = own["responses"][401]["content"]["application/json"]["schema"]
    assert "required" not in declared  # the declared schema wins over the documented error
