"""Fakes for testing services without a database."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from itertools import count
from typing import Generic, TypeVar

from django.db.models import Model

from .._internal.i18n import not_found
from .context import RequestContext
from .errors import NotFound
from .tasks import RecordingTaskQueue

__all__ = ["InMemoryRepository", "RecordingTaskQueue", "make_context"]

ModelT = TypeVar("ModelT", bound=Model)
UserT = TypeVar("UserT")
TenantT = TypeVar("TenantT")


class InMemoryRepository(Generic[ModelT]):
    """A ``Repository`` keeping unsaved model instances in a dict (no database, no signals)."""

    def __init__(self, model: type[ModelT]) -> None:
        self.model = model
        self.items: dict[object, ModelT] = {}
        self._ids = count(1)

    def transaction(self) -> AbstractContextManager[object]:
        return nullcontext()

    def get(self, lookup: object, /, *, field: str = "pk") -> ModelT:
        for instance in self.items.values():
            if getattr(instance, field) == lookup:
                return instance
        raise NotFound(not_found(self.model))

    def add(self, data: Mapping[str, object], /) -> ModelT:
        instance = self.model()
        instance.pk = next(self._ids)
        return self.change(instance, data)

    def change(self, instance: ModelT, data: Mapping[str, object], /) -> ModelT:
        for name, value in data.items():
            setattr(instance, name, value)
        self.items[instance.pk] = instance
        return instance

    def remove(self, instance: ModelT, /) -> None:
        self.items.pop(instance.pk, None)

    async def aget(self, lookup: object, /, *, field: str = "pk") -> ModelT:
        return self.get(lookup, field=field)

    async def aadd(self, data: Mapping[str, object], /) -> ModelT:
        return self.add(data)

    async def achange(self, instance: ModelT, data: Mapping[str, object], /) -> ModelT:
        return self.change(instance, data)

    async def aremove(self, instance: ModelT, /) -> None:
        self.remove(instance)


def make_context(user: UserT, tenant: TenantT, **metadata: str) -> RequestContext[UserT, TenantT]:
    """A ``RequestContext`` for tests: ``make_context(user, None)``.

    :param user: The acting user.
    :param tenant: The tenant, or ``None``.
    :param metadata: String metadata.
    """
    return RequestContext(user=user, tenant=tenant, metadata=metadata)
