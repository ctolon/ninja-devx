"""Repositories: persistence behind a small, HTTP-free interface."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Generic, Protocol, TypeVar, cast

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import router, transaction
from django.db.models import Model, QuerySet

from .._internal.generics import type_arguments
from .._internal.i18n import not_found
from .errors import NotFound
from .persistence import save_instance

__all__ = ["AsyncRepository", "ModelRepository", "Repository"]

ModelT = TypeVar("ModelT", bound=Model)


class Repository(Protocol[ModelT]):
    """Loads and stores one aggregate type. Implement it however your project stores data."""

    def get(self, lookup: object, /, *, field: str = "pk") -> ModelT: ...

    def transaction(self) -> AbstractContextManager[object]:
        """The unit of work services wrap writes in (``transaction.atomic`` for Django)."""
        ...

    def add(self, data: Mapping[str, object], /) -> ModelT: ...

    def change(self, instance: ModelT, data: Mapping[str, object], /) -> ModelT: ...

    def remove(self, instance: ModelT, /) -> None: ...


class AsyncRepository(Protocol[ModelT]):
    async def aget(self, lookup: object, /, *, field: str = "pk") -> ModelT: ...

    async def aadd(self, data: Mapping[str, object], /) -> ModelT: ...

    async def achange(self, instance: ModelT, data: Mapping[str, object], /) -> ModelT: ...

    async def aremove(self, instance: ModelT, /) -> None: ...


class ModelRepository(Generic[ModelT]):
    """The Django ORM repository, sync and async.

    Subclass with the model as argument (``class PostRepository(ModelRepository[Post])``)
    or pass ``model=``. Async methods use the async ORM for reads and one thread hop
    (with the transaction) for writes.
    """

    def __init__(
        self, model: type[ModelT] | None = None, *, validate: bool = True, using: str | None = None
    ) -> None:
        resolved = model or type_arguments(type(self)).get(ModelT)
        if not (isinstance(resolved, type) and issubclass(resolved, Model)):
            raise TypeError(f"{type(self).__qualname__} needs a model: pass model= or subclass it")
        self.model: type[ModelT] = cast(type[ModelT], resolved)
        self.validate = validate
        self.using = using

    def transaction(self) -> AbstractContextManager[object]:
        return transaction.atomic(using=self.using or router.db_for_write(self.model))

    def queryset(self) -> QuerySet[ModelT]:
        """Override to add default filters or joins for every read."""
        manager = self.model._default_manager
        return manager.using(self.using) if self.using else manager.all()

    # --- Sync ----------------------------------------------------------------------

    def get(self, lookup: object, /, *, field: str = "pk") -> ModelT:
        try:
            return self.queryset().get(**{field: lookup})
        except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
            raise NotFound(not_found(self.model)) from exc

    def add(self, data: Mapping[str, object], /) -> ModelT:
        return save_instance(self.model(), data, validate=self.validate, using=self.using)

    def change(self, instance: ModelT, data: Mapping[str, object], /) -> ModelT:
        return save_instance(instance, data, validate=self.validate, using=self.using)

    def remove(self, instance: ModelT, /) -> None:
        instance.delete(using=self.using)

    # --- Async ---------------------------------------------------------------------

    async def aget(self, lookup: object, /, *, field: str = "pk") -> ModelT:
        try:
            return await self.queryset().aget(**{field: lookup})
        except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
            raise NotFound(not_found(self.model)) from exc

    async def aadd(self, data: Mapping[str, object], /) -> ModelT:
        instance: ModelT = await sync_to_async(self.add)(data)
        return instance

    async def achange(self, instance: ModelT, data: Mapping[str, object], /) -> ModelT:
        changed: ModelT = await sync_to_async(self.change)(instance, data)
        return changed

    async def aremove(self, instance: ModelT, /) -> None:
        await sync_to_async(self.remove)(instance)
