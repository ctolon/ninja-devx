"""Liveness and readiness endpoints for load balancers and orchestrators.

::

    mount(api, {"/health": HealthController})          # GET /health/live, GET /health/ready

    class Health(HealthController):
        health_checks = (DatabaseCheck(), CacheCheck(), MigrationsCheck(), MyQueueCheck())

``/live`` only proves the process answers. ``/ready`` runs every check and returns 200 or
503 with the failing checks, so traffic stops before users see errors. Neither requires
authentication.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock, Thread
from typing import ClassVar, Literal, Protocol

from django.core.cache import caches
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest
from ninja import Schema, Status

from ..routing.controller import Controller, ControllerOptions, Scope
from ..routing.operations import get

__all__ = [
    "CacheCheck",
    "CheckResult",
    "DatabaseCheck",
    "HealthCheck",
    "HealthController",
    "HealthReport",
    "MigrationsCheck",
]


class HealthCheck(Protocol):
    @property
    def name(self) -> str:
        """Shown in the report."""
        ...

    def check(self) -> None:
        """Raise when the dependency is not usable."""
        ...


@dataclass(frozen=True, slots=True)
class DatabaseCheck:
    """Runs ``SELECT 1`` on a database connection."""

    alias: str = DEFAULT_DB_ALIAS
    name: str = "database"

    def check(self) -> None:
        with connections[self.alias].cursor() as cursor:
            cursor.execute("SELECT 1")


@dataclass(frozen=True, slots=True)
class CacheCheck:
    """Writes and reads a key in a cache."""

    alias: str = "default"
    name: str = "cache"

    def check(self) -> None:
        cache = caches[self.alias]
        cache.set("ninja_devx:health", "ok", 5)
        if cache.get("ninja_devx:health") != "ok":
            raise RuntimeError("the cache did not return the value it stored")


@dataclass(frozen=True, slots=True)
class MigrationsCheck:
    """Fails while migrations are not applied (useful during deploys)."""

    alias: str = DEFAULT_DB_ALIAS
    name: str = "migrations"

    def check(self) -> None:
        executor = MigrationExecutor(connections[self.alias])
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            raise RuntimeError(f"{len(plan)} unapplied migration(s)")


class CheckResult(Schema):
    name: str
    status: Literal["ok", "error"]
    duration_ms: float
    error: str | None = None


class HealthReport(Schema):
    status: Literal["ok", "error"]
    checks: list[CheckResult]


class _CheckRunner:
    """One in-flight worker per configured check, owned by one mounted controller.

    Timed-out Python threads cannot safely be killed. Keep their slots occupied until
    they return, so repeated probes never allocate an unbounded queue or thread pool.
    """

    def __init__(self, checks: Sequence[HealthCheck], timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("health_timeout must be positive")
        self.checks = tuple(checks)
        self.timeout = timeout
        self._lock = Lock()
        self._futures: list[Future[CheckResult] | None] = [None] * len(checks)

    def run(self) -> list[CheckResult]:
        deadline = time.perf_counter() + self.timeout
        pending: list[tuple[HealthCheck, Future[CheckResult]]] = []
        with self._lock:
            for index, check in enumerate(self.checks):
                future = self._futures[index]
                if future is None or future.done():
                    future = Future[CheckResult]()
                    self._futures[index] = future
                    Thread(
                        target=self._execute,
                        args=(check, future),
                        daemon=True,
                        name="ninja-devx-health",
                    ).start()
                pending.append((check, future))
        results: list[CheckResult] = []
        for check, future in pending:
            try:
                results.append(future.result(timeout=max(0.0, deadline - time.perf_counter())))
            except TimeoutError:
                results.append(
                    CheckResult(
                        name=check.name,
                        status="error",
                        duration_ms=self.timeout * 1000,
                        error="dependency_timeout",
                    )
                )
        return results

    @staticmethod
    def _execute(check: HealthCheck, future: Future[CheckResult]) -> None:
        try:
            result = _CheckRunner._run_check(check)
        except BaseException:
            result = CheckResult(
                name=check.name, status="error", duration_ms=0, error="dependency_unavailable"
            )
        finally:
            # These handles belong to this worker, not the request thread.
            try:
                connections.close_all()
                # Django deliberately keeps in-memory SQLite connections open on
                # close(). These belong to an exiting worker and must be released.
                for connection in connections.all(initialized_only=True):
                    if connection.vendor == "sqlite" and connection.connection is not None:
                        connection.connection.close()
                        connection.connection = None
                caches.close_all()
            except Exception:
                logging.getLogger(__name__).exception("Health worker cleanup failed")
        future.set_result(result)

    @staticmethod
    def _run_check(check: HealthCheck) -> CheckResult:
        started = time.perf_counter()
        try:
            check.check()
        except Exception:
            logging.getLogger(__name__).exception("Readiness check %s failed", check.name)
            return CheckResult(
                name=check.name,
                status="error",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error="dependency_unavailable",
            )
        return CheckResult(
            name=check.name,
            status="ok",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )


class HealthController(Controller):
    """``GET /live`` and ``GET /ready``."""

    options = ControllerOptions(auth=None, tags=["health"], document_errors=False)
    scope = Scope.SINGLETON
    health_checks: ClassVar[Sequence[HealthCheck]] = (DatabaseCheck(),)
    """Checks run concurrently by ``/ready``; results retain this order."""
    health_timeout: ClassVar[float] = 2.0
    """Total readiness deadline in seconds. Hung checks retain their bounded worker slot."""

    def __init__(self) -> None:
        self._runner = _CheckRunner(type(self).health_checks, type(self).health_timeout)

    @get("/live", response=HealthReport, summary="The process is running")
    def live(self, request: HttpRequest) -> HealthReport:
        return HealthReport(status="ok", checks=[])

    @get(
        "/ready", response={200: HealthReport, 503: HealthReport}, summary="Dependencies are usable"
    )
    def ready(self, request: HttpRequest) -> Status[HealthReport]:
        results = self._runner.run()
        healthy = all(result.status == "ok" for result in results)
        report = HealthReport(status="ok" if healthy else "error", checks=results)
        return Status(200 if healthy else 503, report)
