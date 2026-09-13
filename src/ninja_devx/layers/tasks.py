"""Work that must happen after the transaction commits: tasks, events, emails."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ParamSpec, Protocol, runtime_checkable

from django.db import DEFAULT_DB_ALIAS, connections, transaction

__all__ = [
    "Enqueueable",
    "ImmediateTaskQueue",
    "OnCommitTaskQueue",
    "RecordingTaskQueue",
    "TaskQueue",
    "after_commit",
]

P = ParamSpec("P")


@runtime_checkable
class Enqueueable(Protocol[P]):
    """``django.tasks`` tasks (and anything similar): ``task.enqueue(*args, **kwargs)``."""

    def enqueue(self, *args: P.args, **kwargs: P.kwargs) -> object: ...


class TaskQueue(Protocol):
    """Where services send background work; swap implementations in tests."""

    def enqueue(self, task: Enqueueable[P], /, *args: P.args, **kwargs: P.kwargs) -> None: ...

    def call(self, function: Callable[P, object], /, *args: P.args, **kwargs: P.kwargs) -> None: ...


def after_commit(function: Callable[[], object], *, using: str | None = None) -> None:
    """Run ``function`` when the current transaction commits, or now outside one."""
    alias = using or DEFAULT_DB_ALIAS
    if connections[alias].in_atomic_block:
        transaction.on_commit(function, using=alias)
    else:
        function()


@dataclass(frozen=True, slots=True)
class OnCommitTaskQueue:
    """Enqueues after commit (``django.tasks`` arguments must be JSON-serializable: pass ids)."""

    using: str | None = None
    """Database alias whose commit triggers the work."""

    def enqueue(self, task: Enqueueable[P], /, *args: P.args, **kwargs: P.kwargs) -> None:
        after_commit(lambda: task.enqueue(*args, **kwargs), using=self.using)

    def call(self, function: Callable[P, object], /, *args: P.args, **kwargs: P.kwargs) -> None:
        after_commit(lambda: function(*args, **kwargs), using=self.using)


@dataclass(frozen=True, slots=True)
class ImmediateTaskQueue:
    """Runs work right away (scripts, or tests that want the side effects)."""

    def enqueue(self, task: Enqueueable[P], /, *args: P.args, **kwargs: P.kwargs) -> None:
        task.enqueue(*args, **kwargs)

    def call(self, function: Callable[P, object], /, *args: P.args, **kwargs: P.kwargs) -> None:
        function(*args, **kwargs)


@dataclass(slots=True)
class RecordingTaskQueue:
    """Records work instead of running it; ``run_all()`` executes it in order."""

    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = field(
        default_factory=list[tuple[object, tuple[object, ...], dict[str, object]]]
    )
    """Recorded ``(task_or_function, args, kwargs)`` in order."""

    def enqueue(self, task: Enqueueable[P], /, *args: P.args, **kwargs: P.kwargs) -> None:
        self.calls.append((task, args, dict(kwargs)))

    def call(self, function: Callable[P, object], /, *args: P.args, **kwargs: P.kwargs) -> None:
        self.calls.append((function, args, dict(kwargs)))

    def run_all(self) -> None:
        calls, self.calls = self.calls, []
        for target, args, kwargs in calls:
            if isinstance(target, Enqueueable):
                target.enqueue(*args, **kwargs)
            elif callable(target):
                target(*args, **kwargs)
