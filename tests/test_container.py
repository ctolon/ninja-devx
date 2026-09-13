import threading
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Generator
from typing import Annotated, Protocol

import pytest
from django.http import HttpRequest

from ninja_devx import CircularDependencyError, Container, DependencyResolutionError


class Repository(ABC):
    @abstractmethod
    def all(self) -> list[str]: ...


class MemoryRepository(Repository):
    def all(self) -> list[str]:
        return ["ada"]


class Service:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository


class Clock:
    pass


def test_autowires_concrete_classes_as_transient():
    container = Container()
    assert isinstance(container.resolve(Clock), Clock)
    assert container.resolve(Clock) is not container.resolve(Clock)


def test_interface_binding_through_the_graph():
    container = Container()
    container.singleton(Repository, MemoryRepository)

    service = container.resolve(Service)
    assert isinstance(service.repository, MemoryRepository)
    assert service.repository is container.resolve(Service).repository


def test_transient_registration():
    container = Container()
    container.transient(Repository, MemoryRepository)
    assert container.resolve(Repository) is not container.resolve(Repository)


def test_singleton_without_implementation():
    container = Container()
    container.singleton(Clock)
    assert container.resolve(Clock) is container.resolve(Clock)


def test_instance_registration():
    container = Container()
    repository = MemoryRepository()
    container.instance(Repository, repository)
    assert container.resolve(Service).repository is repository


def test_factories_are_autowired():
    container = Container()
    container.singleton(Repository, MemoryRepository)

    def make_service(repository: Repository) -> Service:
        return Service(repository)

    container.singleton(Service, make_service)
    assert container.resolve(Service) is container.resolve(Service)


def test_annotated_dependencies_are_unwrapped():
    class Consumer:
        def __init__(self, clock: Annotated[Clock, "metadata"]) -> None:
            self.clock = clock

    assert isinstance(Container().resolve(Consumer).clock, Clock)


def test_defaults_are_kept_unless_the_type_is_registered():
    class Settings:
        def __init__(self, debug: bool = False, clock: Clock | None = None) -> None:
            self.debug = debug
            self.clock = clock

    container = Container()
    assert container.resolve(Settings).debug is False
    assert container.resolve(Settings).clock is None

    container.instance(bool, True)
    assert container.resolve(Settings).debug is True


def test_abstract_dependency_without_provider():
    with pytest.raises(DependencyResolutionError, match=r"Repository .* it is abstract .* Service"):
        Container().resolve(Service)


@pytest.mark.parametrize(
    ("annotation", "reason"),
    [(str, "builtin type"), (Protocol, "Protocol"), (int | None, "not a class")],
)
def test_non_autowirable_dependencies(annotation, reason):
    class Consumer:
        def __init__(self, value) -> None: ...

    Consumer.__init__.__annotations__["value"] = annotation
    with pytest.raises(DependencyResolutionError, match=reason):
        Container().resolve(Consumer)


def test_missing_annotation():
    class Consumer:
        def __init__(self, value) -> None: ...

    with pytest.raises(DependencyResolutionError, match=r"'value' .* missing type annotation"):
        Container().resolve(Consumer)


class First:
    def __init__(self, second: "Second") -> None: ...


class Second:
    def __init__(self, first: First) -> None: ...


def test_circular_dependencies_report_the_chain():
    with pytest.raises(CircularDependencyError, match="First -> Second -> First"):
        Container().resolve(First)


def test_constructor_errors_are_not_masked():
    class Exploding:
        def __init__(self) -> None:
            raise TypeError("boom")

    with pytest.raises(TypeError, match="boom"):
        Container().resolve(Exploding)


def test_override_restores_previous_state():
    container = Container()
    container.singleton(Repository, MemoryRepository)
    original = container.resolve(Repository)
    fake = MemoryRepository()

    with container.override(Repository, fake):
        assert container.resolve(Repository) is fake
    assert container.resolve(Repository) is original

    with container.override(Clock, Clock()):
        pass
    assert container.resolve(Clock) is not container.resolve(Clock)


def test_reregistering_drops_the_cached_singleton():
    container = Container()
    container.singleton(Clock)
    first = container.resolve(Clock)
    container.singleton(Clock)
    assert container.resolve(Clock) is not first


def test_singletons_are_created_once_under_concurrency():
    created = []

    class Slow:
        def __init__(self) -> None:
            time.sleep(0.01)
            created.append(self)

    container = Container()
    container.singleton(Slow)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(container.resolve(Slow))) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert all(result is created[0] for result in results)


# --- Request scope -----------------------------------------------------------


class CurrentUser:
    def __init__(self, request: HttpRequest) -> None:
        self.name = request.headers.get("X-User", "anonymous")


class UnitOfWork:
    pass


class Handler:
    def __init__(self, user: CurrentUser, first: UnitOfWork, second: UnitOfWork) -> None:
        self.user = user
        self.first = first
        self.second = second


def make_request(user: str = "ada") -> HttpRequest:
    request = HttpRequest()
    request.META["HTTP_X_USER"] = user
    return request


def test_request_scope_injects_the_request_and_caches_scoped_services():
    container = Container()
    container.scoped(UnitOfWork)

    with container.request_scope(make_request("ada")) as scope:
        handler = scope.resolve(Handler)
        assert handler.user.name == "ada"
        assert handler.first is handler.second is scope.resolve(UnitOfWork)

    with container.request_scope(make_request("bob")) as scope:
        assert scope.resolve(UnitOfWork) is not handler.first
        assert scope.resolve(CurrentUser).name == "bob"


def test_scoped_services_are_unavailable_outside_a_scope():
    container = Container()
    container.scoped(UnitOfWork)
    with pytest.raises(DependencyResolutionError, match="is scoped"):
        container.resolve(UnitOfWork)
    with pytest.raises(DependencyResolutionError, match="only available inside a request scope"):
        container.resolve(CurrentUser)


def test_singletons_cannot_capture_scoped_services():
    class Cache:
        def __init__(self, unit_of_work: UnitOfWork) -> None: ...

    container = Container()
    container.scoped(UnitOfWork)
    container.singleton(Cache)
    with (
        container.request_scope(make_request()) as scope,
        pytest.raises(DependencyResolutionError, match="is scoped"),
    ):
        scope.resolve(Cache)


def test_request_scoped_controllers_receive_request_services():
    from ninja.testing import TestClient

    from ninja_devx import Controller, get

    class WhoAmI(Controller):
        def __init__(self, user: CurrentUser) -> None:
            self.user = user

        @get("/")
        def index(self, request):
            return {"user": self.user.name}

    client = TestClient(WhoAmI.as_router(container=Container()))
    assert client.get("/", headers={"X-User": "ada"}).json() == {"user": "ada"}


def test_authenticated_user_factory_injects_the_current_user():
    from django.contrib.auth.models import AnonymousUser
    from ninja.errors import AuthenticationError

    from ninja_devx import authenticated_user

    class FakeUser:
        is_authenticated = True

    class Greeter:
        def __init__(self, user: FakeUser) -> None:
            self.user = user

    container = Container()
    container.scoped(FakeUser, authenticated_user(FakeUser))
    request = HttpRequest()
    request.user = FakeUser()  # type: ignore[assignment]
    with container.request_scope(request) as scope:
        assert scope.resolve(Greeter).user is request.user

    anonymous = HttpRequest()
    anonymous.user = AnonymousUser()
    with container.request_scope(anonymous) as scope, pytest.raises(AuthenticationError):
        scope.resolve(Greeter)


# --- Scopes without HTTP, cleanup, async factories and graph checks ---------------


class Connection:
    def __init__(self) -> None:
        self.closed = False


def test_scope_without_a_request_takes_values():
    class Handler:
        def __init__(self, user: CurrentUser) -> None:
            self.user = user

    container = Container()
    fake = object.__new__(CurrentUser)
    fake.name = "task"
    with container.scope({CurrentUser: fake}) as scope:
        assert scope.resolve(Handler).user is fake


def test_generator_factories_clean_up_when_the_scope_closes():
    connections: list[Connection] = []

    def connect() -> Generator[Connection]:
        connection = Connection()
        connections.append(connection)
        yield connection
        connection.closed = True

    container = Container()
    container.scoped(Connection, connect)
    with container.scope() as scope:
        first = scope.resolve(Connection)
        assert scope.resolve(Connection) is first
        assert not first.closed
    assert first.closed


def test_generator_singletons_clean_up_on_close():
    def connect() -> Generator[Connection]:
        connection = Connection()
        yield connection
        connection.closed = True

    container = Container()
    container.singleton(Connection, connect)
    connection = container.resolve(Connection)
    container.close()
    assert connection.closed


async def test_async_factories_and_async_scopes():
    closed: list[str] = []

    async def connect() -> AsyncGenerator[Connection]:
        yield Connection()
        closed.append("connection")

    async def clock() -> float:
        return 1.5

    class Service:
        def __init__(self, connection: Connection, now: float) -> None:
            self.connection = connection
            self.now = now

    container = Container()
    container.scoped(Connection, connect)
    container.instance(float, 0.0)
    container.transient(float, clock)
    async with container.scope() as scope:
        service = await scope.aresolve(Service)
        assert service.now == 1.5
    assert closed == ["connection"]

    with (
        container.scope() as scope,
        pytest.raises(DependencyResolutionError, match="async factory"),
    ):
        scope.resolve(Service)


def test_check_reports_captive_and_async_dependencies_without_building():
    built: list[str] = []

    class Scoped:
        def __init__(self) -> None:
            built.append("scoped")

    class Cache:
        def __init__(self, scoped: Scoped) -> None: ...

    async def make_float() -> float:
        return 1.0

    class NeedsFloat:
        def __init__(self, value: float) -> None: ...

    container = Container()
    container.scoped(Scoped)
    container.singleton(Cache)
    container.transient(float, make_float)

    with pytest.raises(DependencyResolutionError, match=r"singleton depends on the scoped .*"):
        container.check(Cache)
    with pytest.raises(DependencyResolutionError, match="needed by a sync operation"):
        container.check(NeedsFloat)
    container.check(NeedsFloat, asynchronous=True)
    container.check(Scoped)
    with pytest.raises(DependencyResolutionError, match="cannot be auto-wired"):
        container.check(Repository)
    assert built == []


def test_instances_and_overrides_are_checked_against_class_keys():
    from ninja_devx import DependencyResolutionError

    class Base:
        pass

    class Other:
        pass

    container = Container()
    with pytest.raises(DependencyResolutionError, match="Other instance is not a"):
        container.instance(Base, Other())
    with pytest.raises(DependencyResolutionError, match="Other instance is not a"):  # noqa: SIM117
        with container.override(Base, Other()):
            pass
