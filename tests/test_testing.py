import pytest
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Container, Controller, get
from ninja_devx.testing.clients import async_client_for, client_for


class Clock:
    def now(self) -> str:
        return "real"


class FakeClock(Clock):
    def now(self) -> str:
        return "fake"


class TimeController(Controller):
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    @get("/")
    def now(self, request):
        return {"now": self.clock.now()}

    @get("/async")
    async def anow(self, request):
        return {"now": self.clock.now()}


def test_client_for_with_container_override():
    container = Container()
    client = client_for(TimeController, container=container)
    assert isinstance(client, TestClient)
    assert client.get("/").json() == {"now": "real"}
    with container.override(Clock, FakeClock()):
        assert client.get("/").json() == {"now": "fake"}


async def test_async_client_for():
    client = async_client_for(TimeController, container=Container(), auth=None)
    assert isinstance(client, TestAsyncClient)
    assert (await client.get("/async")).json() == {"now": "real"}


def test_client_for_reports_configuration_errors():
    with pytest.raises(Exception, match="pass container"):
        client_for(TimeController)


@pytest.mark.django_db(transaction=True)
async def test_assert_max_hops_counts_thread_switches():
    from asgiref.sync import sync_to_async

    from ninja_devx.testing.clients import assert_max_hops

    def work() -> int:
        return 1

    with assert_max_hops(2) as hops:
        await sync_to_async(work)()
        await sync_to_async(work)()
    assert hops.count == 2

    async def twice() -> None:
        with assert_max_hops(1):
            await sync_to_async(work)()
            await sync_to_async(work)()

    with pytest.raises(AssertionError, match="at most 1 thread hops, 2 ran"):
        await twice()


def test_pytest_plugin_warns_about_async_database_tests(pytester, monkeypatch):
    # The child tests exercise warnings, not the parent's PostgreSQL database.
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    from pathlib import Path

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent))
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.django_db
        async def test_leaky():
            pass

        @pytest.mark.django_db(transaction=True)
        async def test_fine():
            pass
        """
    )
    result = pytester.runpytest_subprocess(
        "-W",
        "error::ninja_devx.exceptions.AsyncDatabaseTestWarning",
        "--ds=tests.settings",
        "-o",
        "asyncio_mode=auto",
        "-p",
        "no:cacheprovider",
    )
    result.assert_outcomes(passed=1, errors=1)
