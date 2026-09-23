"""One transaction across several repositories: a unit of work.

``ModelService`` already wraps each write in ``repository.transaction()``. Use a
``UnitOfWork`` when a use case combines several repository or ORM operations that must
commit or roll back together, independent of any single repository. It is synchronous;
async code runs it through ``sync_to_async`` like any other ``transaction.atomic`` block.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType

from django.db import transaction

__all__ = ["UnitOfWork"]


class UnitOfWork:
    """Wrap a block in one ``transaction.atomic``.

    :param using: Database alias (default: Django's write router per model).
    :param durable: ``True`` when this must be the outermost atomic block.

    ::

        with UnitOfWork():
            orders.add(...)
            stock.change(...)
    """

    def __init__(self, *, using: str | None = None, durable: bool = False) -> None:
        self.using = using
        self.durable = durable
        self._atomic: AbstractContextManager[None] | None = None

    def __enter__(self) -> UnitOfWork:
        if self._atomic is not None:
            raise RuntimeError("UnitOfWork is already active; create one per block")
        self._atomic = transaction.atomic(using=self.using, durable=self.durable)
        self._atomic.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        atomic = self._atomic
        if atomic is None:
            raise RuntimeError("UnitOfWork must be entered before it is exited")
        self._atomic = None
        atomic.__exit__(exc_type, exc, traceback)
