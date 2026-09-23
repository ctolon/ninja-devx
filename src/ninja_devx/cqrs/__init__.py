"""CQRS and DDD building blocks, layered over ``ninja_devx.layers``.

Thin and opt-in: commands and queries are plain messages handled by classes resolved from
the container (``use_case``/``use_query``), domain events are delivered after commit through
a ``TaskQueue``, and a ``UnitOfWork`` groups several repository writes. There is no command
bus or global registry.
"""

from __future__ import annotations

from ..routing.use_cases import QueryHandler, use_case, use_query
from .events import DomainEvent, EventBus
from .messages import Command, Message, Query
from .unit_of_work import UnitOfWork

__all__ = [
    "Command",
    "DomainEvent",
    "EventBus",
    "Message",
    "Query",
    "QueryHandler",
    "UnitOfWork",
    "use_case",
    "use_query",
]
