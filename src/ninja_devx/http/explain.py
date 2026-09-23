"""``X-Query-Count``, ``X-Query-Time`` and ``X-Query-Plan`` headers, for development.

Install it where a paginated or optimized controller is mounted::

    use_middleware(api, QueryExplainMiddleware())             # active only when DEBUG
    use_middleware(api, QueryExplainMiddleware(enabled=True))  # a staff-only diagnostics API

Counts and durations come from ``django.db.connection.queries`` (``CaptureQueriesContext``
semantics: ``force_debug_cursor`` is set for the request, so this works even when ``DEBUG``
is off). ``X-Query-Plan`` lists the ``select_related``/``prefetch_related`` lookups the N+1
planner chose, read from the request attribute it records them on (``QUERY_PLAN_ATTR``, filled
the same way pagination metadata is). SQL text never reaches a header, and every hook is a
no-op when the middleware is inactive, so nothing is captured in production by default.

Database connections are thread-local: an async list evaluated off-thread (``run_sync``,
or the plain ``sync_to_async`` fallback for a queryset without an async paginator) runs its
queries against a connection this middleware never touched, so the count can undercount for
async operations whose ORM access hops threads.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, cast

from django.conf import settings
from django.db import connections
from django.http import HttpRequest, HttpResponseBase

from .middleware import Middleware

__all__ = ["QUERY_PLAN_ATTR", "QueryExplainMiddleware"]

QUERY_PLAN_ATTR: Final = "_ninja_devx_query_plan"
"""Request attribute the N+1 planner fills with ``(select_related, prefetch_related)``
lookup names it chose for the current request."""

_STATE_ATTR: Final = "_ninja_devx_explain_state"

State = dict[str, "tuple[bool, int]"]


class QueryExplainMiddleware(Middleware):
    """Add query diagnostics headers to matching responses.

    :param enabled: Forces the middleware on or off; ``None`` (the default) follows
        ``settings.DEBUG`` on every request, so ``override_settings(DEBUG=...)`` works.
    :param databases: Database aliases to count; defaults to every configured alias.
    """

    def __init__(self, *, enabled: bool | None = None, databases: Sequence[str] = ()) -> None:
        self.enabled = enabled
        self.databases = tuple(databases)

    def _active(self) -> bool:
        return self.enabled if self.enabled is not None else bool(settings.DEBUG)

    def _aliases(self) -> tuple[str, ...]:
        return self.databases or tuple(connections)

    def process_request(self, request: HttpRequest) -> None:
        if not self._active():
            return None
        state: State = {}
        for alias in self._aliases():
            connection = connections[alias]
            state[alias] = (connection.force_debug_cursor, len(connection.queries_log))
            connection.force_debug_cursor = True
        request.__dict__[_STATE_ATTR] = state
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        state = _pop_state(request)
        if state is None:
            return response
        count, seconds = _measure(state)
        if "X-Query-Count" not in response:
            response["X-Query-Count"] = str(count)
        if "X-Query-Time" not in response:
            response["X-Query-Time"] = f"{seconds * 1000:.2f}"
        header = _plan_header(request.__dict__.get(QUERY_PLAN_ATTR))
        if header and "X-Query-Plan" not in response:
            response["X-Query-Plan"] = header
        return response

    def process_exception(self, request: HttpRequest, exception: BaseException) -> None:
        _pop_state(request)  # restores force_debug_cursor; the exception is re-raised as is


def _pop_state(request: HttpRequest) -> State | None:
    state = cast("State | None", request.__dict__.pop(_STATE_ATTR, None))
    if state is None:
        return None
    for alias, (was_active, _start) in state.items():
        connections[alias].force_debug_cursor = was_active
    return state


def _measure(state: State) -> tuple[int, float]:
    count = 0
    seconds = 0.0
    for alias, (_was_active, start) in state.items():
        for entry in list(connections[alias].queries_log)[start:]:
            count += 1
            seconds += float(entry["time"])
    return count, seconds


def _plan_header(plan: object) -> str:
    if not isinstance(plan, tuple):
        return ""
    pair = cast("tuple[object, ...]", plan)
    if len(pair) != 2:
        return ""
    select, prefetch = cast("tuple[Sequence[str], Sequence[str]]", pair)
    parts: list[str] = []
    if select:
        parts.append("select_related=" + ",".join(select))
    if prefetch:
        parts.append("prefetch_related=" + ",".join(prefetch))
    return "; ".join(parts)
