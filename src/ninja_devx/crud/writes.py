"""Database selection and transaction ownership for controller persistence."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from django.db import router, transaction
from django.db.models import Model
from django.http import HttpRequest

from ..routing.hooks import get_operation

if TYPE_CHECKING:
    from .controllers import ModelController

ModelT = TypeVar("ModelT", bound=Model)


def active_database(request: HttpRequest) -> str | None:
    current = vars(request).get("_devx_write_database")
    if isinstance(current, str):
        return current
    operation = get_operation(request)
    return operation.database if operation is not None else None


def database_for_write(controller: ModelController[ModelT], request: HttpRequest) -> str:
    explicit = active_database(request)
    if explicit is not None:
        return explicit
    queryset = controller.get_queryset(request)
    # QuerySet.db consults the read router; _db is only an explicit .using() choice.
    selected: object = getattr(queryset, "_db", None)
    return selected if isinstance(selected, str) else router.db_for_write(controller.get_model())


@contextmanager
def write_scope(controller: ModelController[ModelT], request: HttpRequest) -> Generator[None]:
    alias = controller.write_database(request)
    previous = vars(request).get("_devx_write_database")
    vars(request)["_devx_write_database"] = alias
    try:
        with transaction.atomic(using=alias):
            yield
    finally:
        if previous is None:
            vars(request).pop("_devx_write_database", None)
        else:
            vars(request)["_devx_write_database"] = previous
