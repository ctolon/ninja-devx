"""Nested resources: ``/authors/{author_pk}/articles``."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Final, Protocol, TypeVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Model, QuerySet
from django.http import Http404, HttpRequest
from ninja.params.functions import Path

from .._internal.i18n import not_found
from ..exceptions import ControllerConfigError
from ..routing.bindings import Arguments, ParameterBinding
from .fields import lookup_type, resolve_field

if TYPE_CHECKING:
    from ..dependencies.instances import Invocation

__all__ = ["Parent", "get_parent"]

_REQUEST_ATTR: Final = "_ninja_devx_parent"
QuerySetT = TypeVar("QuerySetT", bound=QuerySet[Model])


@dataclass(frozen=True, slots=True)
class Parent:
    """Scope a model controller to a parent object taken from the URL.

    ``Parent(Author, field="author")`` expects the router to be mounted under a prefix
    containing ``{author_pk}``; every operation then 404s for unknown authors, lists
    only that author's objects and assigns the author on create.
    """

    model: type[Model]
    """The parent model."""
    field: str
    """The child's foreign key to the parent."""
    lookup_field: str = "pk"
    """Parent field matched by the URL segment."""
    param: str | None = None
    """Name of the URL segment (default ``<field>_pk``)."""
    tenant_field: str | None = None
    """Only find parents of the current tenant (``"organization"``): other tenants' parents 404."""

    @property
    def parameter(self) -> str:
        return self.param or f"{self.field}_pk"

    def validate(self, child: type[Model]) -> None:
        related: object = getattr(resolve_field(child, self.field), "related_model", None)
        if related is not self.model:
            raise ControllerConfigError(
                f"{child.__name__}.{self.field} does not point to {self.model.__name__}"
            )

    def binding(self) -> ParameterBinding:
        exposed = inspect.Parameter(
            self.parameter,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[lookup_type(self.model, self.lookup_field), Path()],
        )

        def resolve(invocation: Invocation, arguments: Arguments) -> object:
            request = invocation.request
            lookup = arguments.pop(self.parameter)
            filters: dict[str, object] = {self.lookup_field: lookup}
            if self.tenant_field is not None:
                filters[self.tenant_field] = _tenant_of(invocation)
            try:
                parent = self.model._default_manager.get(**filters)
            except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
                raise Http404(not_found(self.model)) from exc
            setattr(request, _REQUEST_ATTR, parent)
            return parent

        async def aresolve(invocation: Invocation, arguments: Arguments) -> object:
            request = invocation.request
            lookup = arguments.pop(self.parameter)
            filters: dict[str, object] = {self.lookup_field: lookup}
            if self.tenant_field is not None:
                filters[self.tenant_field] = await _atenant_of(invocation)
            try:
                parent = await self.model._default_manager.aget(**filters)
            except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
                raise Http404(not_found(self.model)) from exc
            setattr(request, _REQUEST_ATTR, parent)
            return parent

        return ParameterBinding(
            name=None,
            parameters=(exposed,),
            resolve=resolve,
            aresolve=aresolve,
            documented_errors=frozenset({404}),
        )

    def filter(self, queryset: QuerySetT, request: HttpRequest) -> QuerySetT:
        return queryset.filter(**{self.field: get_parent(request)})


class _TenantAware(Protocol):
    def get_tenant(self, request: HttpRequest) -> object: ...
    async def aget_tenant(self, request: HttpRequest) -> object: ...


def _tenant_of(invocation: Invocation) -> object:
    controller = cast("_TenantAware", invocation.controller)
    return controller.get_tenant(invocation.request)


async def _atenant_of(invocation: Invocation) -> object:
    controller = cast("_TenantAware", invocation.controller)
    return await controller.aget_tenant(invocation.request)


def get_parent(request: HttpRequest) -> Model:
    """The parent object of a nested operation (loaded before the controller runs)."""
    parent: Model | None = getattr(request, _REQUEST_ATTR, None)
    if parent is None:
        raise RuntimeError("get_parent() called outside a nested operation")
    return parent


def parent_bindings(parent: Parent | None) -> Sequence[ParameterBinding]:
    return () if parent is None else (parent.binding(),)
