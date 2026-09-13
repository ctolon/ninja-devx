import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.db import connections
from ninja.testing import TestAsyncClient, TestClient

pytest_plugins = ["pytester"]


@pytest_asyncio.fixture(autouse=True)
async def close_async_worker_connections(request):
    """Ninja's test client bypasses Django's request_finished connection cleanup.

    Close ORM handles in the same thread-sensitive worker which async tests used,
    before PostgreSQL teardown tries to drop the test database.
    """
    yield
    if request.node.get_closest_marker("asyncio") is not None:
        await sync_to_async(connections.close_all)()


@pytest.fixture
def client_for():
    return TestClient


@pytest.fixture
def async_client_for():
    return TestAsyncClient
