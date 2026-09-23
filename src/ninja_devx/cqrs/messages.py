"""Application messages: optional markers for commands and queries.

Subclassing is optional; any frozen dataclass works with ``use_case`` and ``use_query``.
The markers document intent and let type checkers (and ``devx_inspect``) tell a write from
a read.
"""

from __future__ import annotations

__all__ = ["Command", "Message", "Query"]


class Message:
    """Base for an application message."""


class Command(Message):
    """A request that changes state, handled by a ``use_case`` handler."""


class Query(Message):
    """A request that reads state, handled by a ``use_query`` handler."""
