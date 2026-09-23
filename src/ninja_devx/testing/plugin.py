"""pytest plugin (auto-loaded): controller clients and OpenAPI snapshots.

Fixtures:

- ``ninja_client(ControllerOrRouter, user=None, **as_router_kwargs)`` -> test client
- ``ninja_async_client(...)`` -> async test client
- ``ninja_contract(api, include=None)`` -> a schemathesis schema calling the app in-process
  (``pip install ninja-devx[contract]``)
- ``captured_commits(execute=True)`` -> ``ninja_devx.testing.clients.capture_commits``
- ``openapi_snapshot(api, name="openapi")`` compares ``api``'s schema with
  ``__snapshots__/<test module>/<name>.json``; run ``pytest --update-snapshots``
  to accept changes.
- ``strict_queries`` runs the test inside ``django-zeal``, raising on any N+1; skips with a
  clear reason when ``zeal`` (the ``ninja-devx[zeal]`` extra) is not installed.

Async tests that use the database without ``django_db(transaction=True)`` get an
``AsyncDatabaseTestWarning``: their ORM calls run on another thread and connection,
outside the test transaction. Disable with ``ninja_devx_warn_async_db = false`` (ini).

Query-count fixtures come from pytest-django (``django_assert_max_num_queries``).
Django and Ninja are imported lazily: plugins load before settings are configured.
"""

from __future__ import annotations

import inspect
import json
import warnings
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Unpack

import pytest

if TYPE_CHECKING:
    from ninja import NinjaAPI, Router

    from ..dependencies.container import ContainerLike
    from ..routing.controller import Controller, ControllerOptions, Scope
    from .clients import AuthenticatedAsyncClient, AuthenticatedClient

__all__ = ["pytest_addoption", "pytest_runtest_setup"]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="ninja-devx: rewrite OpenAPI snapshots instead of comparing them",
    )
    parser.addini(
        "ninja_devx_warn_async_db",
        type="bool",
        default=True,
        help="ninja-devx: warn when async tests use the database without transaction=True",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if not isinstance(item, pytest.Function) or not inspect.iscoroutinefunction(item.obj):
        return
    if not item.config.getini("ninja_devx_warn_async_db"):
        return
    fixtures = set(item.fixturenames)
    marker = item.get_closest_marker("django_db")
    uses_db = marker is not None or bool(fixtures & {"db", "transactional_db"})
    transactional = "transactional_db" in fixtures or (
        marker is not None
        and bool(marker.kwargs.get("transaction", marker.args[0] if marker.args else False))
    )
    if uses_db and not transactional:
        from ..exceptions import AsyncDatabaseTestWarning

        warnings.warn(
            AsyncDatabaseTestWarning(
                f"{item.nodeid} is async but uses the database without "
                "django_db(transaction=True); ORM calls run on another connection, outside "
                "the test transaction"
            ),
            stacklevel=1,
        )


def _target(
    target: type[Controller] | Router | NinjaAPI,
    container: ContainerLike | None,
    scope: Scope | None,
    options: ControllerOptions,
) -> Router | NinjaAPI:
    from ninja import NinjaAPI, Router

    if isinstance(target, Router | NinjaAPI):
        return target
    return target.as_router(container=container, scope=scope, **options)


@pytest.fixture
def ninja_client() -> Callable[..., AuthenticatedClient]:
    """``ninja_client(ControllerOrRouter, user=None, container=None, scope=None, **options)``."""
    from .clients import AuthenticatedClient

    def factory(
        target: type[Controller] | Router | NinjaAPI,
        *,
        user: object | None = None,
        container: ContainerLike | None = None,
        scope: Scope | None = None,
        **options: Unpack[ControllerOptions],
    ) -> AuthenticatedClient:
        return AuthenticatedClient(_target(target, container, scope, options), user=user)

    return factory


@pytest.fixture
def ninja_async_client() -> Callable[..., AuthenticatedAsyncClient]:
    """``ninja_async_client(ControllerOrRouter, user=None, **options)``: an async test client."""
    from .clients import AuthenticatedAsyncClient

    def factory(
        target: type[Controller] | Router | NinjaAPI,
        *,
        user: object | None = None,
        container: ContainerLike | None = None,
        scope: Scope | None = None,
        **options: Unpack[ControllerOptions],
    ) -> AuthenticatedAsyncClient:
        return AuthenticatedAsyncClient(_target(target, container, scope, options), user=user)

    return factory


@pytest.fixture
def ninja_contract() -> Callable[..., object]:
    """``ninja_contract(api)``: a schemathesis schema for ``schemathesis.pytest.from_fixture``."""
    from .contracts import contract_schema

    return contract_schema


@pytest.fixture
def captured_commits() -> Callable[..., AbstractContextManager[list[Callable[[], object]]]]:
    """``with captured_commits() as callbacks: ...`` runs on-commit work at the end."""
    from .clients import capture_commits

    return capture_commits


@pytest.fixture
def openapi_snapshot(request: pytest.FixtureRequest) -> Callable[..., None]:
    """``openapi_snapshot(api, name="openapi")`` compares the schema with a stored snapshot."""
    update = bool(request.config.getoption("--update-snapshots"))
    module = Path(str(request.path))

    def check(api: NinjaAPI, name: str = "openapi") -> None:
        schema = api.get_openapi_schema(path_prefix="")
        current = json.dumps(schema, indent=2, sort_keys=True, default=str) + "\n"
        path = module.parent / "__snapshots__" / module.stem / f"{name}.json"
        if update or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(current)
            return
        if current != path.read_text():
            pytest.fail(
                f"OpenAPI schema differs from {path}; run pytest --update-snapshots to accept",
                pytrace=False,
            )

    return check


@pytest.fixture
def strict_queries() -> Generator[None]:
    """Raise ``zeal.NPlusOneError`` for any N+1 in this test, regardless of ``ZEAL_RAISE``.

    Skips with a clear reason when ``django-zeal`` is not installed
    (``pip install ninja-devx[zeal]``).
    """
    from ..contrib.nplusone import zeal_installed, zeal_strict

    if not zeal_installed():
        pytest.skip("django-zeal is not installed; add the `zeal` extra to use strict_queries")
    with zeal_strict():
        yield
