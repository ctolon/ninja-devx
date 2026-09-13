"""Test helpers: exercise controllers through Django Ninja's test clients."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Unpack

from asgiref.sync import SyncToAsync, ThreadSensitiveContext
from django.db import DEFAULT_DB_ALIAS, connections
from django.test.utils import CaptureQueriesContext
from ninja import NinjaAPI, Router
from ninja.testing import TestAsyncClient, TestClient
from ninja.testing.client import NinjaResponse

from ..dependencies.container import ContainerLike
from ..routing.controller import Controller, ControllerOptions, Scope

__all__ = [
    "AuthenticatedAsyncClient",
    "AuthenticatedClient",
    "HopCounter",
    "assert_max_hops",
    "assert_max_queries",
    "async_client_for",
    "capture_commits",
    "client_for",
]


class AuthenticatedClient(TestClient):
    """A ``TestClient`` that sends every request as ``user`` unless one is given."""

    def __init__(self, router_or_app: Router | NinjaAPI, *, user: object | None = None) -> None:
        super().__init__(router_or_app)
        self.user = user

    # Same parameters as ``TestClient.request`` (Ninja types them with ``Dict``/``Any``).
    def request(
        self,
        method: str,
        path: str,
        data: dict[str, object] | None = None,
        json: object = None,
        **request_params: object,
    ) -> NinjaResponse:
        if self.user is not None:
            request_params.setdefault("user", self.user)
        response: NinjaResponse = super().request(method, path, data, json, **request_params)  # pyright: ignore[reportUnknownMemberType]
        return response


class AuthenticatedAsyncClient(TestAsyncClient):
    """A ``TestAsyncClient`` that sends every request as ``user`` unless one is given."""

    def __init__(self, router_or_app: Router | NinjaAPI, *, user: object | None = None) -> None:
        super().__init__(router_or_app)
        self.user = user

    async def request(  # type: ignore[override]
        self,
        method: str,
        path: str,
        data: dict[str, object] | None = None,
        json: object = None,
        **request_params: object,
    ) -> NinjaResponse:
        if self.user is not None:
            request_params.setdefault("user", self.user)
        # Like ASGIHandler: sync work of one request shares one thread (and DB connection).
        async with ThreadSensitiveContext():  # type: ignore[no-untyped-call]
            response: NinjaResponse = await super().request(  # pyright: ignore[reportUnknownMemberType]
                method, path, data, json, **request_params
            )
        return response


def client_for(
    controller: type[Controller],
    *,
    user: object | None = None,
    container: ContainerLike | None = None,
    scope: Scope | None = None,
    **options: Unpack[ControllerOptions],
) -> AuthenticatedClient:
    """A test client for ``controller.as_router(...)``; paths are relative to the router.

    ``user`` becomes ``request.user`` for every request (override per request with ``user=``).

    :param controller: The controller class.
    :param user: Sent as ``request.user`` with every request (override per request with ``user=``).
    :param container: Container for ``as_router()``.
    :param scope: Scope for ``as_router()``.
    :param options: ``ControllerOptions`` for ``as_router()``.
    """
    router = controller.as_router(container=container, scope=scope, **options)
    return AuthenticatedClient(router, user=user)


def async_client_for(
    controller: type[Controller],
    *,
    user: object | None = None,
    container: ContainerLike | None = None,
    scope: Scope | None = None,
    **options: Unpack[ControllerOptions],
) -> AuthenticatedAsyncClient:
    """
    :param controller: The controller class.
    :param user: Sent as ``request.user`` with every request.
    :param container: Container for ``as_router()``.
    :param scope: Scope for ``as_router()``.
    :param options: ``ControllerOptions`` for ``as_router()``.
    """
    router = controller.as_router(container=container, scope=scope, **options)
    return AuthenticatedAsyncClient(router, user=user)


@contextmanager
def assert_max_queries(
    limit: int, *, using: str = DEFAULT_DB_ALIAS
) -> Generator[CaptureQueriesContext]:
    """Fail when the block runs more than ``limit`` queries (catches N+1 regressions).

    :param limit: Maximum number of queries in the block.
    :param using: Database alias to watch.
    """
    with CaptureQueriesContext(connections[using]) as context:
        yield context
    if len(context.captured_queries) > limit:
        queries = "\n".join(
            f"{index}. {query['sql']}" for index, query in enumerate(context.captured_queries, 1)
        )
        raise AssertionError(
            f"Expected at most {limit} queries, {len(context.captured_queries)} ran:\n{queries}"
        )


@dataclass(slots=True)
class HopCounter:
    """Thread hops (``sync_to_async`` calls) seen inside ``assert_max_hops``."""

    functions: list[str] = field(default_factory=list[str])

    @property
    def count(self) -> int:
        return len(self.functions)


@contextmanager
def assert_max_hops(limit: int) -> Generator[HopCounter]:
    """Fail when the block switches from the event loop to a thread more than ``limit`` times.

    Each hop costs tens of microseconds and serializes on the request's thread::

        with assert_max_hops(1):
            await client.post("/", json=payload)   # async CRUD create: one hop

    :param limit: Maximum ``sync_to_async`` thread hops in the block.
    """
    counter = HopCounter()
    original: Callable[..., Awaitable[object]] = vars(SyncToAsync)["__call__"]

    async def counting(self: SyncToAsync[..., object], *args: object, **kwargs: object) -> object:
        function: object = getattr(self, "func", None)
        name: object = getattr(function, "__qualname__", None)  # never repr(): it may query
        counter.functions.append(name if isinstance(name, str) else type(function).__name__)
        return await original(self, *args, **kwargs)

    setattr(SyncToAsync, "__call__", counting)  # noqa: B010
    try:
        yield counter
    finally:
        setattr(SyncToAsync, "__call__", original)  # noqa: B010
    if counter.count > limit:
        hops = "\n".join(f"{index}. {name}" for index, name in enumerate(counter.functions, 1))
        raise AssertionError(f"Expected at most {limit} thread hops, {counter.count} ran:\n{hops}")


@contextmanager
def capture_commits(
    *, using: str = DEFAULT_DB_ALIAS, execute: bool = True
) -> Generator[list[Callable[[], object]]]:
    """Collect ``transaction.on_commit`` callbacks (``after_commit``, ``OnCommitTaskQueue``).

    In a test transaction nothing ever commits; with ``execute=True`` the callbacks run
    when the block exits, as they would after a real commit::

        with capture_commits() as callbacks:
            client.post("/orders", json=payload)
        assert len(callbacks) == 1          # the confirmation email was scheduled

    :param using: Database alias.
    :param execute: Run the callbacks when the block exits.
    """
    from django.test import TestCase

    with TestCase.captureOnCommitCallbacks(using=using, execute=execute) as callbacks:
        yield callbacks
