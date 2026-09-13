"""Selectors: read queries with a name, shared by endpoints, tasks and services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

from django.db.models import Model, QuerySet

__all__ = ["Selector"]

FilterT_contra = TypeVar("FilterT_contra", contravariant=True)
ModelT_co = TypeVar("ModelT_co", bound=Model, covariant=True)


class Selector(Protocol[FilterT_contra, ModelT_co]):
    """Returns a queryset (paginated, ordered and optimized by list endpoints) or a list."""

    def __call__(self, filters: FilterT_contra, /) -> QuerySet[ModelT_co] | Sequence[ModelT_co]: ...
