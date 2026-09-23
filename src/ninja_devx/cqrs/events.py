"""Domain events and an explicit, in-app event bus.

There is no global registry or automatic scanning: the application owns an ``EventBus``
(usually a container singleton), subscribes handlers in its wiring, and publishes events
from use cases. Delivery goes through a :class:`~ninja_devx.layers.TaskQueue`, so handlers
run after the current transaction commits.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

from ..layers.tasks import TaskQueue

__all__ = ["DomainEvent", "EventBus"]

EventT = TypeVar("EventT", bound="DomainEvent")


class DomainEvent:
    """Base for a domain event; use a frozen dataclass subclass."""


class EventBus:
    """A registry that delivers domain events after the current transaction commits.

    Register it in the container and publish through it::

        container.singleton(EventBus, EventBus(OnCommitTaskQueue()))
        bus = container.resolve(EventBus)
        bus.subscribe(OrderPlaced, notify_followers)
        bus.publish(OrderPlaced(order_id=order.pk))

    Handlers are plain callables ``(event) -> object``. Run in-process, they enqueue
    ``django.tasks`` work or defer to the outbox for durable, cross-process delivery.
    """

    def __init__(self, tasks: TaskQueue) -> None:
        self._tasks = tasks
        self._handlers: dict[type[DomainEvent], list[Callable[[object], object]]] = {}

    def subscribe(self, event: type[EventT], handler: Callable[[EventT], object]) -> None:
        """Call ``handler`` for every published instance of ``event``.

        :param event: The event class (subclasses are not matched automatically).
        :param handler: A callable receiving the event.
        """
        self._handlers.setdefault(event, []).append(cast("Callable[[object], object]", handler))

    def publish(self, event: DomainEvent) -> None:
        """Run every handler subscribed to ``type(event)`` after commit.

        :param event: The event instance to deliver.
        """
        for handler in self._handlers.get(type(event), ()):
            self._tasks.call(handler, event)

    def clear(self) -> None:
        """Forget all subscriptions; useful when re-wiring or in tests."""
        self._handlers.clear()
