"""Mandatory queryset boundaries and representation loading policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from django.db import models
from django.db.models import Model, QuerySet
from django.http import HttpRequest
from pydantic import BaseModel

from ..configuration.settings import class_setting, get_settings
from ..routing.hooks import get_operation
from ..security.auth import request_user
from ..serialization.visibility import response_shape
from .optimization import optimize_queryset
from .writes import active_database

if TYPE_CHECKING:
    from .controllers import ModelController

ModelT = TypeVar("ModelT", bound=Model)


def scoped_queryset(controller: ModelController[ModelT], request: HttpRequest) -> QuerySet[ModelT]:
    queryset = controller.get_queryset(request)
    if (alias := active_database(request)) is not None:
        queryset = queryset.using(alias)
    cls = type(controller)
    if cls.tenant_field is not None:
        queryset = queryset.filter(**{cls.tenant_field: controller.get_tenant(request)})
    if cls.object_permissions is not None and cls.object_permissions.filter_lists:
        queryset = cls.object_permissions.filter(request, queryset)
    if cls.parent is not None:
        queryset = cls.parent.filter(queryset, request)
    owner = cls.owner_field
    if owner is not None:
        if cls.scope_queryset_to_owner:
            queryset = queryset.filter(**{owner: request_user(request)})
        if "__" in owner:  # IsOwner walks the relations: load them in the same query
            queryset = queryset.select_related(owner.rsplit("__", 1)[0])
    schema = cls.output_schema()
    optimize = class_setting(cls, "optimize_queries", get_settings().optimize_queries)
    if schema is not None and optimize:
        queryset = optimize_queryset(
            queryset, schema, only=optimize == "only", expand=expanded_fields(request, schema)
        )
    operation = get_operation(request)
    if operation is not None and operation.is_async and get_settings().async_fetch_mode == "raise":
        fetch_mode = getattr(queryset, "fetch_mode", None)  # Django 6.1+
        if fetch_mode is not None:
            queryset = fetch_mode(getattr(models, "FETCH_RAISE"))  # noqa: B009
    return queryset


def expanded_fields(request: HttpRequest, schema: type[BaseModel]) -> frozenset[str]:
    shape = response_shape(request)
    return shape.expand if shape is not None and issubclass(schema, shape.schema) else frozenset()
