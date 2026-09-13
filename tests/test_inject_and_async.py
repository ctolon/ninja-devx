import logging
import time
import warnings
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated

import pytest
from django.contrib.auth.models import User
from django.http import HttpRequest
from django.test import override_settings
from ninja import NinjaAPI
from ninja.streaming import JSONL
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import (
    Container,
    Controller,
    ControllerConfigError,
    ControllerOptions,
    DependencyResolutionError,
    LoggingHook,
    OperationInfo,
    Scope,
    get,
    get_operation,
    post,
)
from ninja_devx.dependencies.injection import Inject, Resolve, resolve
from ninja_devx.exceptions import AsyncLazyAccessError, BlockingCallWarning, MixedPathWarning
from ninja_devx.routing.operations import async_variant


class Clock:
    def now(self) -> str:
        return "12:00"


class Counter:
    def __init__(self) -> None:
        self.value = 0


def client_ip(request: HttpRequest) -> str:
    return request.META.get("REMOTE_ADDR", "")


async def async_client_ip(request: HttpRequest) -> str:
    return "async:" + request.META.get("REMOTE_ADDR", "")


class InjectingController(Controller):
    @get("/now")
    def now(
        self, request: HttpRequest, clock: Inject[Clock], ip: Annotated[str, Resolve(client_ip)]
    ):
        return {"now": clock.now(), "ip": ip}

    @get("/async-now")
    async def anow(
        self,
        request: HttpRequest,
        clock: Inject[Clock],
        ip: Annotated[str, Resolve(async_client_ip)],
    ):
        return {"now": clock.now(), "ip": ip}

    @get("/counter")
    def counter(self, request: HttpRequest, first: Inject[Counter], second: Inject[Counter]):
        first.value += 1
        return {"same": first is second, "via_helper": resolve(request, Counter) is first}


def test_inject_and_resolve():
    container = Container()
    container.scoped(Counter)
    client = TestClient(InjectingController.as_router(container=container))
    assert client.get("/now").json() == {"now": "12:00", "ip": "127.0.0.1"}
    assert client.get("/counter").json() == {"same": True, "via_helper": True}


async def test_inject_in_async_operations_uses_async_factories():
    async def make_clock() -> AsyncGenerator[Clock]:
        yield Clock()

    container = Container()
    container.scoped(Clock, make_clock)
    container.scoped(Counter)

    class AsyncOnly(Controller):
        @get("/")
        async def index(self, request: HttpRequest, clock: Inject[Clock]):
            return {"now": clock.now()}

    response = await TestAsyncClient(AsyncOnly.as_router(container=container)).get("/")
    assert response.json() == {"now": "12:00"}

    with pytest.raises(DependencyResolutionError, match="needed by a sync operation"):
        InjectingController.as_router(container=container)


def test_inject_without_container_fails_at_startup():
    with pytest.raises(ControllerConfigError, match=r"needs as_router\(container=\.\.\.\)"):
        InjectingController.as_router()


def test_injected_parameters_are_not_in_openapi():
    api = NinjaAPI()
    api.add_router("", InjectingController.as_router(container=Container()))
    operation = api.get_openapi_schema(path_prefix="")["paths"]["/now"]["get"]
    assert operation["parameters"] == []


def test_captive_dependencies_fail_when_the_router_is_built():
    class Cache:
        def __init__(self, counter: Counter) -> None: ...

    class UsesCache(Controller):
        def __init__(self, cache: Cache) -> None: ...

        @get("/")
        def index(self, request): ...

    container = Container()
    container.scoped(Counter)
    container.singleton(Cache)
    with pytest.raises(DependencyResolutionError, match="singleton depends on the scoped"):
        UsesCache.as_router(container=container)


def test_singleton_controllers_still_get_request_scoped_injections():
    container = Container()
    container.scoped(Counter)
    client = TestClient(InjectingController.as_router(container=container, scope=Scope.SINGLETON))
    assert client.get("/counter").json()["same"] is True


# --- Modes -----------------------------------------------------------------------------


class Dual(Controller):
    mode = "auto"

    @get("/")
    def index(self, request):
        return {"mode": "sync"}

    @async_variant(index)
    async def aindex(self, request):
        return {"mode": "async"}


async def test_mode_selects_the_async_variant():
    assert TestClient(Dual.as_router()).get("/").json() == {"mode": "sync"}
    with override_settings(NINJA_DEVX={"ASYNC_MODE": "async"}):
        router = Dual.as_router()
    assert (await TestAsyncClient(router).get("/")).json() == {"mode": "async"}

    class ForcedAsync(Dual):
        mode = "async"

    assert (await TestAsyncClient(ForcedAsync.as_router()).get("/")).json() == {"mode": "async"}


def test_mixed_paths_warn():
    class Mixed(Controller):
        @get("/")
        def read(self, request): ...

        @post("/")
        async def write(self, request): ...

    with pytest.warns(MixedPathWarning, match="mixes sync and async"):
        Mixed.as_router()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Mixed.as_router(allow_mixed_path=True)


# --- Hooks, streams, blocking and lazy access ----------------------------------------------


class AsyncTiming:
    events: list[str] = []

    @asynccontextmanager
    async def around_async(
        self, request: HttpRequest, operation: OperationInfo, /
    ) -> AsyncGenerator[None]:
        AsyncTiming.events.append("start")
        yield
        AsyncTiming.events.append("end")


class SlowSyncHook:
    @contextmanager
    def around(self, request: HttpRequest, operation: OperationInfo, /) -> Generator[None]:
        time.sleep(0.02)
        yield


async def test_async_hooks_wrap_whole_streams(caplog):
    AsyncTiming.events.clear()

    class Streams(Controller):
        options = ControllerOptions(hooks=[AsyncTiming(), LoggingHook()])

        @get("/items", response=JSONL[int])
        async def items(self, request):
            AsyncTiming.events.append("item")
            yield 1

    with caplog.at_level(logging.INFO, logger="ninja_devx"):
        response = await TestAsyncClient(Streams.as_router()).get("/items")
    assert response.content == b"1\n"
    assert AsyncTiming.events == ["start", "item", "end"]
    assert "streams_items" in caplog.text


def test_sync_stream_hooks_cover_the_body(caplog):
    class Streams(Controller):
        options = ControllerOptions(hooks=[LoggingHook()])

        @get("/items", response=JSONL[int])
        def items(self, request):
            yield 1
            time.sleep(0.03)
            yield 2

    with caplog.at_level(logging.INFO, logger="ninja_devx"):
        response = TestClient(Streams.as_router()).get("/items")
    assert response.content == b"1\n2\n"
    (record,) = caplog.records
    assert record.duration_ms >= 30  # type: ignore[attr-defined]  # the hook timed the body


def test_async_only_hooks_are_rejected_on_sync_operations():
    class Rejected(Controller):
        options = ControllerOptions(hooks=[AsyncTiming()])

        @get("/")
        def index(self, request): ...

    with pytest.raises(ControllerConfigError, match="only implement around_async"):
        Rejected.as_router()


async def test_blocking_sync_hooks_warn_in_async_operations():
    class Slow(Controller):
        options = ControllerOptions(hooks=[SlowSyncHook()])

        @get("/")
        async def index(self, request):
            return {}

    with override_settings(NINJA_DEVX={"WARN_BLOCKING_MS": 5}):
        router = Slow.as_router()
    with pytest.warns(BlockingCallWarning, match="blocked the event loop"):
        await TestAsyncClient(router).get("/")


@pytest.mark.django_db(transaction=True)
async def test_sync_orm_access_in_async_operations_explains_itself():
    class Lazy(Controller):
        @get("/")
        async def index(self, request):
            return {"count": User.objects.count()}

    with pytest.raises(AsyncLazyAccessError, match="select_related"):
        await TestAsyncClient(Lazy.as_router()).get("/")


@pytest.mark.django_db(transaction=True)
async def test_run_sync_and_run_atomic():
    class Writer(Controller):
        @post("/")
        async def create(self, request):
            await self.run_atomic(User.objects.create, username="atomic")
            return {"count": await self.run_sync(User.objects.count)}

    response = await TestAsyncClient(Writer.as_router()).post("/")
    assert response.json() == {"count": 1}


def test_operation_metadata():
    class RequiresScope:
        def __init__(self, scope: str) -> None:
            self.scope = scope

    class Scoped(Controller):
        options = ControllerOptions(meta=[RequiresScope("read")])

        @get("/", meta=[RequiresScope("write")])
        def index(self, request):
            operation = get_operation(request)
            assert operation is not None
            required = operation.meta(RequiresScope)
            return {"scope": required.scope if required else None}

    assert TestClient(Scoped.as_router()).get("/").json() == {"scope": "write"}
