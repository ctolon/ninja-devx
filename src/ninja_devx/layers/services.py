"""Services: business operations over repositories, callable from sync and async code."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Generic, TypeVar, cast

from django.db.models import Model

from .._internal.generics import type_arguments
from .dual import dual
from .repository import ModelRepository, Repository

__all__ = ["ModelService"]

ModelT = TypeVar("ModelT", bound=Model)


class ModelService(Generic[ModelT]):
    """Create, update and delete through a repository; override to add business rules.

    ``CRUDController`` sends its writes here when ``service_class`` is set::

        class PostService(ModelService[Post]):
            @dual
            def create(self, data: Mapping[str, object]) -> Post:
                return super().create({**data, "slug": slugify(data["title"])})

    Dependencies come from the container (``PostService(repository: PostRepository)``);
    without a registered repository a ``ModelRepository`` for the model is used.
    """

    def __init__(
        self, repository: Repository[ModelT] | None = None, *, model: type[ModelT] | None = None
    ) -> None:
        if repository is None:
            resolved = model or type_arguments(type(self)).get(ModelT)
            repository = ModelRepository(cast("type[ModelT] | None", resolved))
        self.repository: Repository[ModelT] = repository

    @dual
    def create(self, data: Mapping[str, object]) -> ModelT:
        with self.repository.transaction():
            return self.repository.add(data)

    @dual
    def update(self, instance: ModelT, data: Mapping[str, object]) -> ModelT:
        with self.repository.transaction():
            return self.repository.change(instance, data)

    @dual
    def delete(self, instance: ModelT) -> None:
        with self.repository.transaction():
            self.repository.remove(instance)
