"""``django.tasks`` integration (Django 6.0+): deliver an event as soon as it is committed.

::

    publish("order.created", {...}, owner=order.owner, queue=OnCommitTaskQueue())

``deliver_event_task`` is ``None`` on older Django versions; pass your own task to
``publish(task=...)`` there (Celery: a ``@shared_task`` calling ``deliver_event``).
"""

from __future__ import annotations

import importlib

from ...layers.tasks import Enqueueable
from .outbox import deliver_event

__all__ = ["deliver_event_task"]


def _deliver(event_id: str, using: str) -> None:
    deliver_event(event_id, using=using)


def _make_task() -> Enqueueable[[str, str]] | None:
    try:
        tasks = importlib.import_module("django.tasks")
    except ImportError:
        return None
    made: object = tasks.task(_deliver)
    return made if isinstance(made, Enqueueable) else None


deliver_event_task: Enqueueable[[str, str]] | None = _make_task()
"""The ``django.tasks`` task delivering one event (``None`` before Django 6.0)."""
