from collections.abc import Iterator

import pytest
import svcs
from asgiref.sync import sync_to_async
from dishka import Provider, Scope, from_context, make_async_container, make_container, provide
from django.http import HttpRequest
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, ControllerConfigError, DependencyResolutionError, Inject, get
from ninja_devx import Scope as ControllerScope
from ninja_devx.contrib.dishka import AsyncDishkaResolver, DishkaResolver, provide_controllers
from ninja_devx.contrib.svcs import SvcsResolver


class Greeter:
    def __init__(self, user: str) -> None:
        self.user = user

    def greet(self) -> str:
        return f"hello {self.user}"


class GreetController(Controller):
    def __init__(self, greeter: Greeter) -> None:
        self.greeter = greeter

    @get("/")
    def index(self, request):
        return {"message": self.greeter.greet()}

    @get("/async")
    async def index_async(self, request):
        return {"message": self.greeter.greet()}


# --- dishka ------------------------------------------------------------------

closed: list[str] = []


class AppProvider(Provider):
    request = from_context(provides=HttpRequest, scope=Scope.REQUEST)

    @provide(scope=Scope.REQUEST)
    def greeter(self, request: HttpRequest) -> Iterator[Greeter]:
        yield Greeter(request.headers.get("X-User", "anonymous"))
        closed.append("greeter")

    @provide(scope=Scope.REQUEST)
    def controller(self, greeter: Greeter) -> GreetController:
        return GreetController(greeter)


def test_dishka_request_scope_with_cleanup():
    closed.clear()
    router = GreetController.as_router(container=DishkaResolver(make_container(AppProvider())))
    response = TestClient(router).get("/", headers={"X-User": "ada"})
    assert response.json() == {"message": "hello ada"}
    assert closed == ["greeter"]


class AsyncGreetController(GreetController):
    def index(self, request):  # undecorated override: drop the sync operation
        raise NotImplementedError


class AsyncAppProvider(AppProvider):
    @provide(scope=Scope.REQUEST)
    def async_controller(self, greeter: Greeter) -> AsyncGreetController:
        return AsyncGreetController(greeter)


async def test_async_dishka_request_scope():
    closed.clear()
    container = AsyncDishkaResolver(make_async_container(AsyncAppProvider()))
    router = AsyncGreetController.as_router(container=container)
    response = await TestAsyncClient(router).get("/async", headers={"X-User": "ada"})
    assert response.json() == {"message": "hello ada"}
    assert closed == ["greeter"]


def test_async_only_containers_reject_sync_operations():
    container = AsyncDishkaResolver(make_async_container(AsyncAppProvider()))
    with pytest.raises(ControllerConfigError, match="container is async-only"):
        GreetController.as_router(container=container)


def test_dishka_singleton_controller_uses_the_root_container():
    class SingletonProvider(Provider):
        @provide(scope=Scope.APP)
        def greeter(self) -> Greeter:
            return Greeter("app")

        @provide(scope=Scope.APP)
        def controller(self, greeter: Greeter) -> GreetController:
            return GreetController(greeter)

    resolver = DishkaResolver(make_container(SingletonProvider()))
    router = GreetController.as_router(container=resolver, scope=ControllerScope.SINGLETON)
    assert TestClient(router).get("/").json() == {"message": "hello app"}


# --- svcs --------------------------------------------------------------------


def make_registry() -> svcs.Registry:
    registry = svcs.Registry()

    def greeter(container: svcs.Container) -> Iterator[Greeter]:
        request = container.get(HttpRequest)
        yield Greeter(request.headers.get("X-User", "anonymous"))
        closed.append("greeter")

    registry.register_factory(Greeter, greeter)

    def controller(container: svcs.Container) -> GreetController:
        return GreetController(container.get(Greeter))

    registry.register_factory(GreetController, controller)
    return registry


def test_svcs_request_scope_with_cleanup():
    closed.clear()
    router = GreetController.as_router(container=SvcsResolver(make_registry()))
    response = TestClient(router).get("/", headers={"X-User": "ada"})
    assert response.json() == {"message": "hello ada"}
    assert closed == ["greeter"]


async def test_svcs_async_request_scope():
    closed.clear()
    router = GreetController.as_router(container=SvcsResolver(make_registry()))
    response = await TestAsyncClient(router).get("/async", headers={"X-User": "bob"})
    assert response.json() == {"message": "hello bob"}
    assert closed == ["greeter"]


# --- dishka: one resolver for sync and async, startup checks ---------------------------


class Clock:
    def now(self) -> str:
        return "noon"


class DualController(Controller):
    def __init__(self, greeter: Greeter) -> None:
        self.greeter = greeter

    @get("/")
    def index(self, request, clock: Inject[Clock]):
        return {"message": self.greeter.greet(), "at": clock.now()}

    @get("/async")
    async def index_async(self, request, clock: Inject[Clock]):
        return {"message": self.greeter.greet(), "at": clock.now()}


def greeter_from(request: HttpRequest) -> Greeter:
    return Greeter(request.headers.get("X-User", "anonymous"))


def dual_provider() -> Provider:
    provider = Provider(scope=Scope.REQUEST)
    provider.from_context(provides=HttpRequest, scope=Scope.REQUEST)
    provider.provide(greeter_from)
    provider.provide(Clock, scope=Scope.APP)
    provide_controllers(provider, [DualController])
    return provider


async def test_dishka_resolver_serves_sync_and_async_operations():
    provider = dual_provider()
    resolver = DishkaResolver(
        make_container(provider), async_container=make_async_container(provider)
    )
    router = DualController.as_router(container=resolver)
    response = await TestAsyncClient(router).get("/async", headers={"X-User": "ada"})
    assert response.json() == {"message": "hello ada", "at": "noon"}
    sync_response = await sync_to_async(TestClient(router).get)("/", headers={"X-User": "bob"})
    assert sync_response.json() == {"message": "hello bob", "at": "noon"}


def test_dishka_missing_controller_fails_at_startup():
    provider = Provider(scope=Scope.REQUEST)
    provider.from_context(provides=HttpRequest, scope=Scope.REQUEST)
    provider.provide(Clock)
    with pytest.raises(DependencyResolutionError, match="provide_controllers"):
        DualController.as_router(container=DishkaResolver(make_container(provider)))


def test_dishka_missing_injected_dependency_fails_at_startup():
    provider = Provider(scope=Scope.REQUEST)
    provider.from_context(provides=HttpRequest, scope=Scope.REQUEST)
    provider.provide(greeter_from)
    provide_controllers(provider, [DualController])
    with pytest.raises(DependencyResolutionError, match="cannot provide Clock"):
        DualController.as_router(container=DishkaResolver(make_container(provider)))
