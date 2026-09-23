"""TaskQueue adapters for Celery, Dramatiq, RQ, Taskiq, Temporal and FastStream.

The built-in :class:`~ninja_devx.layers.OnCommitTaskQueue` defers work through
``django.tasks``. These adapters let services keep depending on the ``TaskQueue`` protocol
while a framework executes the work.

Framework tasks are callables exposing a deferral method: Celery ``.delay``, Dramatiq
``.send`` and Taskiq ``.kiq`` are detected automatically. RQ and Temporal need a
queue/client, passed to the constructor::

    container.singleton(TaskQueue, CeleryTaskQueue())
    container.singleton(TaskQueue, RQTaskQueue(redis_queue))
    container.singleton(TaskQueue, TemporalTaskQueue(temporal_client))
    container.singleton(TaskQueue, FastStreamTaskQueue(broker, queue="tasks"))

``DeferredTaskQueue`` adapts any ``defer(function, args, kwargs)`` callable when no
framework-specific adapter fits.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar, Protocol, cast

__all__ = [
    "CeleryTaskQueue",
    "DeferredTaskQueue",
    "DramatiqTaskQueue",
    "FastStreamTaskQueue",
    "FrameworkTaskQueue",
    "RQTaskQueue",
    "TaskiqTaskQueue",
    "TemporalTaskQueue",
]


class _Queue(Protocol):
    def enqueue(self, function: object, /, *args: object, **kwargs: object) -> object: ...


class _Client(Protocol):
    def start_workflow(self, workflow: object, /, *args: object, **kwargs: object) -> object: ...


class _Broker(Protocol):
    async def publish(self, message: object, /, *args: object, **kwargs: object) -> object: ...


Defer = Callable[[object, "tuple[object, ...]", "dict[str, object]"], object]


class DeferredTaskQueue:
    """TaskQueue over any ``defer(function, args, kwargs)`` callable.

    Use it to adapt an async broker or a custom transport without a framework-specific
    adapter.
    """

    def __init__(self, defer: Defer) -> None:
        self._defer = defer

    def enqueue(self, task: object, /, *args: object, **kwargs: object) -> None:
        self._defer(task, args, kwargs)

    def call(self, function: object, /, *args: object, **kwargs: object) -> None:
        self._defer(function, args, kwargs)


class FastStreamTaskQueue(DeferredTaskQueue):
    """FastStream: publish ``{"task", "args", "kwargs"}`` through a broker.

    ``broker.publish`` is async; the adapter wraps it with ``async_to_sync`` so a sync
    service can enqueue. Pass a FastStream broker (``KafkaBroker``, ``NatsBroker``, ...)::

        from faststream.nats import NatsBroker

        container.singleton(TaskQueue, FastStreamTaskQueue(broker, queue="tasks"))
    """

    def __init__(self, broker: object, *, queue: str = "") -> None:
        from asgiref.sync import async_to_sync

        destination = queue
        sender = async_to_sync(cast("_Broker", broker).publish)

        def defer(function: object, args: tuple[object, ...], kwargs: dict[str, object]) -> object:
            message: dict[str, object] = {
                "task": getattr(function, "__name__", None) or type(function).__name__,
                "args": list(args),
                "kwargs": kwargs,
            }
            if destination:
                return sender(message, queue=destination)
            return sender(message)

        super().__init__(defer)


class FrameworkTaskQueue:
    """Defer work using a task framework's callable conventions.

    :param queue: An RQ-style queue exposing ``enqueue(function, *args, **kwargs)``.
    :param client: A Temporal-style client exposing ``start_workflow(workflow, *args)``.
    :param methods: Attribute names tried on the target callable, in order.
    """

    DEFAULT_METHODS: ClassVar[tuple[str, ...]] = ("delay", "send", "kiq")

    def __init__(
        self,
        *,
        queue: object | None = None,
        client: object | None = None,
        methods: Sequence[str] | None = None,
    ) -> None:
        self.queue = queue
        self.client = client
        self.methods = tuple(methods) if methods is not None else type(self).DEFAULT_METHODS

    def enqueue(self, task: object, /, *args: object, **kwargs: object) -> None:
        self._defer(task, args, kwargs)

    def call(self, function: object, /, *args: object, **kwargs: object) -> None:
        self._defer(function, args, kwargs)

    def _defer(self, target: object, args: tuple[object, ...], kwargs: dict[str, object]) -> None:
        for name in self.methods:
            method: object = getattr(target, name, None)
            if callable(method):
                method(*args, **kwargs)
                return
        if self.queue is not None:
            cast("_Queue", self.queue).enqueue(target, *args, **kwargs)
        elif self.client is not None:
            cast("_Client", self.client).start_workflow(target, *args, **kwargs)
        else:
            raise TypeError(f"{target!r} cannot be deferred by {type(self).__name__}")


class CeleryTaskQueue(FrameworkTaskQueue):
    """Celery: ``task.delay(*args, **kwargs)``."""

    DEFAULT_METHODS: ClassVar[tuple[str, ...]] = ("delay",)


class DramatiqTaskQueue(FrameworkTaskQueue):
    """Dramatiq: ``actor.send(*args, **kwargs)``."""

    DEFAULT_METHODS: ClassVar[tuple[str, ...]] = ("send",)


class TaskiqTaskQueue(FrameworkTaskQueue):
    """Taskiq: ``task.kiq(*args, **kwargs)``."""

    DEFAULT_METHODS: ClassVar[tuple[str, ...]] = ("kiq",)


class RQTaskQueue(FrameworkTaskQueue):
    """RQ: ``queue.enqueue(function, *args, **kwargs)``."""

    def __init__(self, queue: object) -> None:
        super().__init__(queue=queue, methods=())


class TemporalTaskQueue(FrameworkTaskQueue):
    """Temporal: ``client.start_workflow(workflow, *args, **kwargs)``."""

    def __init__(self, client: object) -> None:
        super().__init__(client=client, methods=())
