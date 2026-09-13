"""Generic model controllers and composable CRUD mixins (sync and async from one source).

The generic arguments are the configuration::

    class ArticleController(CRUDController[Article, ArticleOut, ArticleIn]):
        owner_field = "author"
        search_fields = ("title", "body")
        ordering_fields = ("created", "title")

``mode`` picks sync or async operations (``"auto"`` follows ``NINJA_DEVX["ASYNC_MODE"]``).
Writes go through a ``ModelService`` (``service_class``) and its repository, so business
rules live outside HTTP; the controller only adds what comes from the request (owner,
parent) and handles permissions, 404s and responses.
"""

from __future__ import annotations

import builtins
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import ClassVar, Final, Generic, Literal, Never, TypeVar, cast

from asgiref.sync import sync_to_async
from django.core.checks import CheckMessage, Error, Warning
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connections, router
from django.db.models import Model, QuerySet
from django.http import Http404, HttpRequest, HttpResponseBase
from ninja import FilterSchema, Status
from ninja.errors import AuthenticationError
from ninja.pagination import PaginationBase, paginate
from pydantic import BaseModel

from .._internal.generics import defined_in, substitute, type_arguments
from .._internal.i18n import not_found
from .._internal.types import ResponseSpec
from ..configuration.settings import class_setting, get_settings
from ..dependencies.container import ContainerLike
from ..dependencies.instances import get_invocation
from ..exceptions import ControllerConfigError
from ..http.conditional import (
    ETag,
    check_if_match,
    conditional,
    not_modified,
    remember_etag,
    representation_tag,
)
from ..layers.repository import ModelRepository
from ..layers.selectors import Selector
from ..layers.services import ModelService
from ..routing.bindings import ParameterBinding
from ..routing.controller import Controller, ControllerOptions
from ..routing.hooks import OperationInfo
from ..routing.operations import OperationSpec, async_variant, delete, get, patch, post, put
from ..security.auth import arequest_user, request_user
from ..security.object_permissions import ObjectPermissions
from ..security.permissions import IsAuthenticated, IsOwner
from ..security.tenancy import TenantResolver, acurrent_tenant_for, current_tenant_for
from ..serialization.schemas import Patch, PatchData
from .annotations import (
    Filters,
    Lookup,
    Ordering,
    OrderingSchema,
    controller_model,
    has_lookup,
    with_path_converter,
)
from .fields import resolve_field
from .filters import FilterFields
from .nested import Parent, get_parent, parent_bindings
from .optimization import optimize_queryset
from .pagination import CursorPagination
from .persistence import model_field, unknown_fields
from .scoping import expanded_fields, scoped_queryset
from .shaping import partial_schema, shape_bindings
from .writes import database_for_write, write_scope

__all__ = [
    "CRUDController",
    "CreateHooks",
    "CreateMixin",
    "DestroyMixin",
    "ListConfig",
    "ListMixin",
    "ModelController",
    "ReadOnlyModelController",
    "RetrieveMixin",
    "UpdateMixin",
]

ModelT = TypeVar("ModelT", bound=Model)
OutT = TypeVar("OutT", bound=BaseModel)
InT = TypeVar("InT", bound=BaseModel)

# TypeVars used as values (in ``response=``) are substituted when the router is built.
_OUT: Final[object] = OutT
OUT_SCHEMA: Final = cast(type[BaseModel], _OUT)
_IN: Final[object] = InT
IN_SCHEMA: Final = cast(type[BaseModel], _IN)


class ModelController(Controller, Generic[ModelT]):
    """Base for controllers backed by a Django model.

    Every hook receives ``request`` explicitly, so subclasses stay safe with
    ``Scope.SINGLETON``. Class attributes left unset fall back to ``settings.NINJA_DEVX``.
    """

    mode: ClassVar[Literal["sync", "async", "auto"]] = "auto"
    """Which implementation of each operation to register: ``"auto"`` follows
    ``NINJA_DEVX["ASYNC_MODE"]``."""
    lookup_field: ClassVar[str] = "pk"
    """Model field matched by the lookup path segment (``"slug"``, ``"uuid"``)."""
    lookup_param: ClassVar[str] = "pk"
    """Name of the lookup path segment: ``"slug"`` exposes ``/{slug}``. Declared paths keep
    using ``{pk}``."""
    lookup_converter: ClassVar[bool] = False
    """Use Django path converters (``{int:pk}``): invalid ids get Django's 404 instead of a
    JSON 422, and static routes of routers mounted later are never shadowed."""
    owner_field: ClassVar[str | None] = None
    """Path to the user (``"author"``, ``"customer__user"``): enforced with ``IsAuthenticated``
    and ``IsOwner``; a direct foreign key is also set on create."""
    scope_queryset_to_owner: ClassVar[bool] = False
    """Also restrict every query (including lists) to the current user's objects."""
    tenant_field: ClassVar[str | None] = None
    """The field pointing at the tenant (``"organization"``, ``"project__organization"``):
    every query is filtered by the current tenant, which is also set on create."""
    tenant_resolver: ClassVar[TenantResolver | None] = None
    """Returns the request's tenant (sync or async); defaults to
    ``NINJA_DEVX["TENANT_RESOLVER"]``. Assign it with ``staticmethod(...)``."""
    tenant_context: ClassVar[object | None] = None
    """A ``RequestContext[User, Tenant]`` key resolved from the container; its ``tenant`` is
    used. Defaults to ``NINJA_DEVX["TENANT_CONTEXT"]``."""
    object_permissions: ClassVar[ObjectPermissions | None] = None
    """Per-object Django permissions (grants or django-guardian): object checks by HTTP method
    and, with ``filter_lists``, querysets limited to viewable objects."""
    sparse_fields: ClassVar[bool] = False
    """Let clients pick response fields with ``?fields=id,title`` (list and retrieve); the
    output schema needs the ``FieldVisibility`` mixin. ``Expandable`` relations add
    ``?expand=``."""
    fields_param: ClassVar[str] = "fields"
    """Query parameter of ``sparse_fields``."""
    expand_param: ClassVar[str] = "expand"
    """Query parameter listing the ``Expandable`` relations to embed."""
    etag: ClassVar[ETag | None] = None
    """Conditional requests: ``ETag`` and 304 on reads, ``If-Match`` and 412 on writes."""
    parent: ClassVar[Parent | None] = None
    """Nest under a parent from the URL, e.g. ``Parent(Author, field="author")``."""
    service_class: ClassVar[type[object] | None] = None
    """The ``ModelService`` subclass handling writes, resolved from the container when there
    is one. Defaults to a ``ModelService`` over a ``ModelRepository`` of the model."""
    validate_model: ClassVar[bool] = True
    """Run ``Model.full_clean()`` before saving (for the default service); errors are 422."""
    refresh_after_write: ClassVar[bool] = True
    """Re-fetch through ``scoped_queryset()`` after writes so responses see its joins."""
    optimize_queries: ClassVar[bool | Literal["only"]] = True
    """Join/prefetch what the output schema renders; ``"only"`` also restricts columns."""
    """Derive ``select_related``/``prefetch_related`` from the output schema."""

    # --- Configuration -----------------------------------------------------------------

    def authorize_replay(
        self, request: HttpRequest, operation: OperationInfo, arguments: Mapping[str, object]
    ) -> object:
        """Recheck current ownership, grants, tenancy and deletion before a replay."""
        if self.lookup_param in arguments:
            self.get_object(request, arguments[self.lookup_param])
        return super().authorize_replay(request, operation, arguments)

    @classmethod
    def get_model(cls) -> type[ModelT]:
        model = type_arguments(cls).get(ModelT)  # type: ignore[misc]
        if not (isinstance(model, type) and issubclass(model, Model)):
            raise ControllerConfigError(
                f"{cls.__qualname__} must parameterize its model, e.g. ModelController[Article]"
            )
        return cast(type[ModelT], model)

    @classmethod
    def output_schema(cls) -> type[BaseModel] | None:
        schema = type_arguments(cls).get(OutT)
        return schema if isinstance(schema, type) and issubclass(schema, BaseModel) else None

    @classmethod
    def input_schema(cls) -> type[BaseModel] | None:
        schema = type_arguments(cls).get(InT)
        return schema if isinstance(schema, type) and issubclass(schema, BaseModel) else None

    @classmethod
    def merged_options(cls, overrides: ControllerOptions | None = None) -> ControllerOptions:
        merged = super().merged_options(overrides)
        if cls.object_permissions is not None:
            merged["permissions"] = (*merged.get("permissions", ()), cls.object_permissions)
        if cls.owner_field is not None:
            merged["permissions"] = (
                IsAuthenticated(),
                *merged.get("permissions", ()),
                IsOwner(cls.owner_field),
            )
        return merged

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        spec = super().customize_operation(name, spec)
        if cls.owner_field is not None:
            resolve_field(cls.get_model(), cls.owner_field)  # fails at startup when misspelled
        if cls.tenant_field is not None:
            resolve_field(cls.get_model(), cls.tenant_field)
        if has_lookup(spec.path, cls):
            spec = replace(spec, path=with_path_converter(spec.path, cls))
        output = cls.output_schema()
        response = spec.options.get("response")
        if cls.sparse_fields and name in {"list", "retrieve"} and output and response is not None:
            # ``?fields=`` may leave any field out: document the response as partial
            partial = cast("ResponseSpec", substitute(response, {OutT: partial_schema(output)}))
            spec = spec.with_options(response=partial)
        config = cls.etag
        if config is not None and name in _CONDITIONAL_OPERATIONS:
            from_body = name == "list" and config.lists
            spec = spec.with_options(
                decorators=(*spec.options.get("decorators", ()), conditional(from_body=from_body))
            )
        return spec

    @classmethod
    def operation_bindings(cls, name: str, spec: OperationSpec) -> Sequence[ParameterBinding]:
        if cls.parent is not None:
            cls.parent.validate(cls.get_model())
        shaping = shape_bindings(cls) if name in {"list", "retrieve"} else ()
        return (
            *super().operation_bindings(name, spec),
            *parent_bindings(cls.parent),
            *shaping,
        )

    @classmethod
    def documented_errors(cls, name: str, spec: OperationSpec) -> frozenset[int]:
        errors = super().documented_errors(name, spec)
        if has_lookup(spec.path, cls):
            errors |= {404}
        if cls.tenant_field is not None:
            errors |= {403}  # no tenant for the request
        config = cls.etag
        if config is not None and name in _CONDITIONAL_WRITES:
            errors |= {412, 428} if config.require_if_match else {412}
        return errors

    # --- Queries -----------------------------------------------------------------------

    def get_queryset(self, request: HttpRequest) -> QuerySet[ModelT]:
        """Override to scope and optimize queries (tenancy, annotations...)."""
        return self.get_model()._default_manager.all()

    def get_tenant(self, request: HttpRequest) -> object:
        """The current tenant (resolved once per request); 403 when there is none."""
        resolver, context = type(self)._tenant_sources()
        return current_tenant_for(
            request, resolver=resolver, context=context, invocation=get_invocation(request)
        )

    async def aget_tenant(self, request: HttpRequest) -> object:
        resolver, context = type(self)._tenant_sources()
        return await acurrent_tenant_for(
            request, resolver=resolver, context=context, invocation=get_invocation(request)
        )

    @classmethod
    def _tenant_sources(cls) -> tuple[TenantResolver | None, object | None]:
        raw: object = inspect.getattr_static(cls, "tenant_resolver", None)
        if isinstance(raw, staticmethod):
            raw = raw.__func__
        settings = get_settings()
        resolver = cast("TenantResolver | None", raw) or settings.tenant_resolver
        return resolver, cls.tenant_context or settings.tenant_context

    async def aprepare_request(self, request: HttpRequest) -> None:
        """Load what sync code will need without blocking the event loop: the user and tenant."""
        await arequest_user(request)
        cls = type(self)
        if cls.tenant_field is not None:
            await self.aget_tenant(request)
        if cls.object_permissions is not None:
            await _warm_content_type(cls.get_model())

    def scoped_queryset(self, request: HttpRequest) -> QuerySet[ModelT]:
        """``get_queryset()`` restricted to the tenant, parent and owner, with N+1 optimizations."""
        return scoped_queryset(self, request)

    def get_object(self, request: HttpRequest, lookup: object, *, lock: bool = False) -> ModelT:
        """Fetch one object (404 when missing) and enforce object permissions."""
        queryset = self.scoped_queryset(request)
        if lock:
            alias = self.write_database(request)
            queryset = queryset.using(alias)
            queryset = (
                queryset.select_for_update(of=("self",))
                if connections[alias].features.has_select_for_update_of
                else queryset.select_for_update()
            )
        return self.get_object_from(request, queryset, lookup)

    def get_object_from(
        self, request: HttpRequest, queryset: QuerySet[ModelT], lookup: object
    ) -> ModelT:
        try:
            instance = queryset.get(**{self.lookup_field: lookup})
        except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
            raise Http404(not_found(self.get_model())) from exc
        self.check_object_permissions(request, instance)
        return instance

    async def aget_object(self, request: HttpRequest, lookup: object) -> ModelT:
        await self.aprepare_request(request)
        try:
            instance = await self.scoped_queryset(request).aget(**{self.lookup_field: lookup})
        except (ObjectDoesNotExist, ValueError, TypeError, DjangoValidationError) as exc:
            raise Http404(not_found(self.get_model())) from exc
        await self.acheck_object_permissions(request, instance)
        return instance

    # --- Conditional requests ----------------------------------------------------------

    def object_etag(self, request: HttpRequest, instance: ModelT) -> str:
        """The entity tag of ``instance`` (``etag`` must be set)."""
        config = type(self).etag or ETag()
        return representation_tag(
            instance, type(self).output_schema(), config, instance.pk, request=request
        )

    def conditional_object(
        self, request: HttpRequest, instance: ModelT
    ) -> ModelT | HttpResponseBase:
        """``instance``, or a 304 when the client's ``If-None-Match`` still matches it."""
        if type(self).etag is None:
            return instance
        tag = self.object_etag(request, instance)
        remember_etag(request, tag)
        return not_modified(request, tag) or instance

    def check_preconditions(self, request: HttpRequest, instance: ModelT) -> None:
        """Before a write: 412 when ``If-Match`` is stale, 428 when it is required and missing."""
        config = type(self).etag
        if config is not None:
            tag = self.object_etag(request, instance)
            check_if_match(request, tag, required=config.require_if_match)

    def written(self, request: HttpRequest, instance: ModelT) -> ModelT:
        """After a write: send the new ``ETag``."""
        if type(self).etag is not None:
            remember_etag(request, self.object_etag(request, instance))
        return instance

    def refresh(self, request: HttpRequest, instance: ModelT) -> ModelT:
        """Verify persisted scope and optionally reload the result through its write database."""
        queryset = (
            self.scoped_queryset(request).using(self.write_database(request)).filter(pk=instance.pk)
        )
        fresh = queryset.first()
        if fresh is None:
            raise Http404(not_found(self.get_model()))
        self.check_object_permissions(request, fresh)
        if class_setting(type(self), "refresh_after_write", get_settings().refresh_after_write):
            instance = fresh
        return instance

    # --- Writes (through the service layer) --------------------------------------------

    def write_database(self, request: HttpRequest) -> str:
        """Operation alias, explicit queryset alias, or the model's write router."""
        return database_for_write(self, request)

    def get_service(self, request: HttpRequest) -> ModelService[ModelT]:
        """The write service: ``service_class`` from this call's container, or the default."""
        cls = type(self)
        service_class = cls.service_class
        if service_class is None:
            validate = class_setting(cls, "validate_model", get_settings().validate_model)
            return ModelService(
                ModelRepository(
                    cls.get_model(), validate=validate, using=self.write_database(request)
                )
            )
        invocation = get_invocation(request)
        if invocation is not None and invocation.resolver is not None:
            resolved: object = invocation.resolver.resolve(service_class)
            service = cast("ModelService[ModelT]", resolved)
        else:
            service = cast("ModelService[ModelT]", service_class())
        repository = getattr(service, "repository", None)
        if isinstance(repository, ModelRepository):
            typed_repository = cast("ModelRepository[ModelT]", repository)
            target = typed_repository.using or router.db_for_write(typed_repository.model)
            if target != self.write_database(request):
                raise ControllerConfigError(
                    f"{cls.__qualname__}: service repository uses {target!r}; "
                    f"controller writes use {self.write_database(request)!r}"
                )
        return service

    def context_data(self, request: HttpRequest) -> dict[str, object]:
        """Fields set from the request on create: the tenant, the owner and the parent."""
        cls = type(self)
        data: dict[str, object] = {}
        if cls.tenant_field is not None and "__" not in cls.tenant_field:
            data[cls.tenant_field] = self.get_tenant(request)
        if cls.owner_field is not None and "__" not in cls.owner_field:
            user = request_user(request)
            if user is None:
                raise AuthenticationError()
            data[cls.owner_field] = user
        if cls.parent is not None:
            data[cls.parent.field] = get_parent(request)
        return data

    def perform_update(
        self, request: HttpRequest, instance: ModelT, data: Mapping[str, object]
    ) -> ModelT:
        return self.get_service(request).update(instance, data)

    def perform_destroy(self, request: HttpRequest, instance: ModelT) -> None:
        self.get_service(request).delete(instance)

    @classmethod
    def checks(cls, container: ContainerLike | None = None) -> list[CheckMessage]:
        messages = super().checks(container)
        model = cls.get_model()
        schema = cls.output_schema()
        if schema is not None:
            for name, info in schema.model_fields.items():
                attribute = info.alias if isinstance(info.alias, str) else name
                resolved = hasattr(schema, f"resolve_{name}") or "." in attribute
                if resolved or model_field(model, attribute) is not None:
                    continue
                if not hasattr(model, attribute):
                    messages.append(
                        Warning(
                            f"{cls.__qualname__}: {schema.__name__}.{name} is not a field "
                            f"or attribute of {model.__name__}",
                            hint=f"Add resolve_{name}() to the schema, an alias, or a "
                            "model property; otherwise serialization fails.",
                            obj=cls,
                            id="ninja_devx.W003",
                        )
                    )
        if schema is not None:
            for name, info in schema.model_fields.items():
                hidden = any(type(item).__name__ == "VisibleTo" for item in info.metadata)
                if hidden and info.is_required():
                    messages.append(
                        Warning(
                            f"{cls.__qualname__}: {schema.__name__}.{name} can be hidden by "
                            "VisibleTo but is required",
                            hint="Declare it optional (`T | None = None`) so OpenAPI and clients "
                            "know it may be missing or null.",
                            obj=cls,
                            id="ninja_devx.W005",
                        )
                    )
        service_class = cls.service_class
        if service_class is not None and container is None:
            required = [
                parameter.name
                for parameter in inspect.signature(service_class).parameters.values()
                if parameter.default is inspect.Parameter.empty
                and parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
            ]
            if required:
                messages.append(
                    Error(
                        f"{cls.__qualname__}: service_class {service_class.__qualname__} "
                        f"needs {', '.join(required)} but no container is given",
                        hint="Mount with as_router(container=...) or give the parameters defaults.",
                        obj=cls,
                        id="ninja_devx.E004",
                    )
                )
        return messages

    @classmethod
    def check_input_schema(cls, hook: str) -> None:
        """Fail at startup when the input schema does not match the model.

        Skipped when ``hook`` or ``service_class`` is customized, since that code may
        map the fields itself.
        """
        schema = cls.input_schema()
        if schema is None or cls.service_class is not None:
            return
        if defined_in(cls, hook, through_wrappers=True) not in (ModelController, CreateHooks):
            return
        model = cls.get_model()
        if unknown := unknown_fields(model, schema.model_fields):
            raise ControllerConfigError(
                f"{cls.__qualname__}: {schema.__name__} fields {unknown} do not exist on "
                f"{model.__name__}; override {hook}() or set service_class to map them"
            )
        context_fields = {
            name
            for name in (cls.owner_field, cls.parent and cls.parent.field, cls.tenant_field)
            if name
        }
        if overlap := sorted(
            name
            for name in schema.model_fields
            if (field := model_field(model, name)) is not None and field.name in context_fields
        ):
            raise ControllerConfigError(
                f"{cls.__qualname__}: {schema.__name__} must not accept {overlap}; "
                "they are set from the request"
            )


async def _warm_content_type(model: type[Model]) -> None:
    """Fill Django's content type cache off the event loop (object permission queries use it)."""
    from django.contrib.contenttypes.models import ContentType

    await sync_to_async(ContentType.objects.get_for_model)(model)


_CONDITIONAL_WRITES: Final = frozenset({"update", "partial_update", "destroy"})
_CONDITIONAL_OPERATIONS: Final = _CONDITIONAL_WRITES | {"list", "retrieve"}


class ListConfig(ModelController[ModelT], Generic[ModelT, OutT]):
    """Filtering, search, ordering, pagination and selectors for list endpoints."""

    filter_schema: ClassVar[type[FilterSchema] | None] = None
    """An explicit Ninja ``FilterSchema`` for ``GET /`` (instead of generated filters)."""
    search_fields: ClassVar[Sequence[str]] = ()
    """Fields searched with ``icontains`` by the ``search_param`` query parameter."""
    search_param: ClassVar[str] = "search"
    """Name of the search query parameter."""
    filter_fields: ClassVar[FilterFields] = MappingProxyType({})
    """Generated typed filters: ``{"status": ("exact",), "created": ("gte", "lte")}``."""
    ordering_fields: ClassVar[Sequence[str]] = ()
    """Fields the client may order by (``?ordering=-created``), validated as an enum."""
    ordering_param: ClassVar[str] = "ordering"
    """Name of the ordering query parameter."""
    default_ordering: ClassVar[Sequence[str]] = ()
    """Ordering when the client sends none; must be among ``ordering_fields``."""
    pagination_class: ClassVar[type[PaginationBase] | None] = None
    """A Ninja pagination class (or ``CursorPagination``); defaults to
    ``NINJA_DEVX["PAGINATION_CLASS"]``."""
    pagination_options: ClassVar[Mapping[str, object]] = MappingProxyType({})
    """Keyword arguments for the pagination class (``{"page_size": 50}``)."""
    selector_class: ClassVar[type[Selector[Never, Model]] | None] = None
    """A selector (resolved from the container when there is one) replacing ``get_queryset``
    for lists. Querysets it returns are still ordered, paginated and optimized."""

    @classmethod
    def checks(cls, container: ContainerLike | None = None) -> list[CheckMessage]:
        messages = super().checks(container)
        model = cls.get_model()
        paths: list[tuple[str, str]] = [
            *(("search_fields", name) for name in cls.search_fields),
            *(("filter_fields", name) for name in cls.filter_fields),
            *(("ordering_fields", name) for name in cls.ordering_fields),
            *(("default_ordering", name.removeprefix("-")) for name in cls.default_ordering),
        ]
        for attribute, path in paths:
            try:
                resolve_field(model, path)
            except ControllerConfigError:
                messages.append(
                    Error(
                        f"{cls.__qualname__}.{attribute}: {model.__name__} has no field {path!r}",
                        obj=cls,
                        id="ninja_devx.E002",
                    )
                )
        return messages

    def list_queryset(
        self, request: HttpRequest, filters: FilterSchema, ordering: OrderingSchema
    ) -> QuerySet[ModelT] | builtins.list[ModelT]:
        selector_class = type(self).selector_class
        if selector_class is None:
            return self.filter_queryset(request, self.scoped_queryset(request), filters, ordering)
        invocation = get_invocation(request)
        selector: Selector[Never, Model] = (
            invocation.resolver.resolve(selector_class)
            if invocation is not None and invocation.resolver is not None
            else selector_class()
        )
        result = cast("Selector[FilterSchema, Model]", selector)(filters)
        if not isinstance(result, QuerySet):
            raise ControllerConfigError(
                f"{type(self).__qualname__}.selector_class must return a QuerySet; "
                "materialized results cannot preserve controller security scopes"
            )
        if result.model is not self.get_model():
            raise ControllerConfigError("The selector returned a QuerySet for another model")
        # Intersect with the complete controller scope, including user overrides and
        # soft deletion. Preserve selector annotations/joins and lazy evaluation.
        queryset = cast("QuerySet[ModelT]", result).filter(
            pk__in=self.scoped_queryset(request).values("pk")
        )
        schema = type(self).output_schema()
        optimize = class_setting(type(self), "optimize_queries", get_settings().optimize_queries)
        if schema is not None and optimize:
            queryset = optimize_queryset(
                queryset, schema, only=optimize == "only", expand=expanded_fields(request, schema)
            )
        return self.order_queryset(queryset, ordering)

    def filter_queryset(
        self,
        request: HttpRequest,
        queryset: QuerySet[ModelT],
        filters: FilterSchema,
        ordering: OrderingSchema,
    ) -> QuerySet[ModelT]:
        return self.order_queryset(filters.filter(queryset), ordering)

    def order_queryset(
        self, queryset: QuerySet[ModelT], ordering: OrderingSchema
    ) -> QuerySet[ModelT]:
        order_by = ordering.values() or self.default_ordering
        return queryset.order_by(*order_by) if order_by else queryset

    @classmethod
    def pagination(cls) -> type[PaginationBase] | None:
        return class_setting(cls, "pagination_class", get_settings().pagination_class)

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        spec = super().customize_operation(name, spec)
        pagination = cls.pagination()
        if name == "list" and pagination is not None:
            if issubclass(pagination, CursorPagination):
                cls._check_cursor_ordering()
            options = dict(cls.pagination_options)
            decorators = (paginate(pagination, **options), *spec.options.get("decorators", ()))
            spec = spec.with_options(decorators=decorators)
        return spec

    @classmethod
    def _check_cursor_ordering(cls) -> None:
        model = cls.get_model()
        names = [*cls.ordering_fields, *(name.removeprefix("-") for name in cls.default_ordering)]
        if nullable := sorted({name for name in names if resolve_field(model, name).null}):
            raise ControllerConfigError(
                f"{cls.__qualname__}: cursor pagination cannot order by nullable fields {nullable}"
            )


class ListMixin(ListConfig[ModelT, OutT], Generic[ModelT, OutT]):
    """``GET /`` with filtering, search, ordering and pagination."""

    @get("/", response=builtins.list[OUT_SCHEMA])  # type: ignore[valid-type]
    def list(
        self, request: HttpRequest, filters: Filters, ordering: Ordering
    ) -> QuerySet[ModelT] | builtins.list[ModelT]:
        return self.list_queryset(request, filters, ordering)

    @async_variant(list)
    async def alist(
        self, request: HttpRequest, filters: Filters, ordering: Ordering
    ) -> QuerySet[ModelT] | builtins.list[ModelT]:
        await self.aprepare_request(request)
        result = self.list_queryset(request, filters, ordering)
        if self.pagination() is not None or not isinstance(result, QuerySet):
            return result  # Ninja's async paginators evaluate the page
        return await sync_to_async(_evaluate)(result)


def _evaluate(queryset: QuerySet[ModelT]) -> builtins.list[ModelT]:
    return builtins.list(queryset)


class RetrieveMixin(ModelController[ModelT], Generic[ModelT, OutT]):
    """``GET /{pk}``."""

    @get("/{pk}", response=OUT_SCHEMA)
    def retrieve(self, request: HttpRequest, pk: Lookup) -> ModelT | HttpResponseBase:
        return self.conditional_object(request, self.get_object(request, pk))

    @async_variant(retrieve)
    async def aretrieve(self, request: HttpRequest, pk: Lookup) -> ModelT | HttpResponseBase:
        return self.conditional_object(request, await self.aget_object(request, pk))


class CreateHooks(ModelController[ModelT], Generic[ModelT, InT]):
    """``perform_create`` typed with the input schema, shared by every create operation."""

    def perform_create(self, request: HttpRequest, payload: InT) -> ModelT:
        """Create through the service with the payload plus the owner and parent."""
        data = {**payload.model_dump(), **self.context_data(request)}
        return self.get_service(request).create(data)

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        if name in {"create", "bulk_create"}:
            cls.check_input_schema("perform_create")
            owner = cls.owner_field
            if (
                owner is not None
                and "__" in owner
                and defined_in(cls, "perform_create", through_wrappers=True) is CreateHooks
            ):
                raise ControllerConfigError(
                    f"{cls.__qualname__}: owner_field {owner!r} follows a relation, so the "
                    "owner cannot be set automatically; override perform_create()"
                )
            tenant = cls.tenant_field
            parent = cls.parent
            through_parent = (
                parent is not None
                and parent.tenant_field is not None
                and tenant is not None
                and tenant.split("__", 1)[0] == parent.field
            )
            if (
                tenant is not None
                and "__" in tenant
                and not through_parent
                and defined_in(cls, "perform_create", through_wrappers=True) is CreateHooks
            ):
                raise ControllerConfigError(
                    f"{cls.__qualname__}: tenant_field {tenant!r} follows a relation; scope the "
                    "parent with Parent(..., tenant_field=...) or override perform_create()"
                )
        return super().customize_operation(name, spec)


class CreateMixin(CreateHooks[ModelT, InT], Generic[ModelT, OutT, InT]):
    """``POST /`` returning 201."""

    @post("/", response={201: OUT_SCHEMA})
    def create(self, request: HttpRequest, payload: InT) -> Status[ModelT]:
        return Status(201, self._create(request, payload))

    @async_variant(create)
    async def acreate(self, request: HttpRequest, payload: InT) -> Status[ModelT]:
        await self.aprepare_request(request)
        return Status(201, await self.run_sync(self._create, request, payload))  # one hop

    def _create(self, request: HttpRequest, payload: InT) -> ModelT:
        with write_scope(self, request):
            return self.refresh(request, self.perform_create(request, payload))


class UpdateMixin(ModelController[ModelT], Generic[ModelT, OutT, InT]):
    """``PUT /{pk}`` (full) and ``PATCH /{pk}`` (only the fields sent)."""

    @put("/{pk}", response=OUT_SCHEMA)
    def update(self, request: HttpRequest, pk: Lookup, payload: InT) -> ModelT:
        return self._update(request, pk, payload.model_dump())

    @patch("/{pk}", response=OUT_SCHEMA)
    def partial_update(self, request: HttpRequest, pk: Lookup, payload: Patch[InT]) -> ModelT:
        return self._update(request, pk, payload)

    @async_variant(update)
    async def aupdate(self, request: HttpRequest, pk: Lookup, payload: InT) -> ModelT:
        await self.aprepare_request(request)
        return await self.run_sync(self._update, request, pk, payload.model_dump())

    @async_variant(partial_update)
    async def apartial_update(
        self, request: HttpRequest, pk: Lookup, payload: Patch[InT]
    ) -> ModelT:
        await self.aprepare_request(request)
        return await self.run_sync(self._update, request, pk, payload)

    def _update(self, request: HttpRequest, lookup: object, data: Mapping[str, object]) -> ModelT:
        """Load, update and reload in one go (one thread hop in async mode)."""
        # Hold the write connection's row lock from the current-version read through
        # persistence. An atomic block alone does not protect a stale earlier read.
        with write_scope(self, request):
            instance = self.get_object(request, lookup, lock=type(self).etag is not None)
            self.check_preconditions(request, instance)
            updated = self.refresh(request, self.perform_update(request, instance, data))
            return self.written(request, updated)

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        if name == "update":
            cls.check_input_schema("perform_update")
        return super().customize_operation(name, spec)


class DestroyMixin(ModelController[ModelT], Generic[ModelT]):
    """``DELETE /{pk}`` returning 204."""

    @delete("/{pk}", response={204: None})
    def destroy(self, request: HttpRequest, pk: Lookup) -> Status[None]:
        with write_scope(self, request):
            instance = self.get_object(request, pk, lock=type(self).etag is not None)
            self.check_preconditions(request, instance)
            self.perform_destroy(request, instance)
        return Status(204, None)

    @async_variant(destroy)
    async def adestroy(self, request: HttpRequest, pk: Lookup) -> Status[None]:
        await self.aprepare_request(request)
        return await self.run_sync(self.destroy, request, pk)


class ReadOnlyModelController(
    ListMixin[ModelT, OutT], RetrieveMixin[ModelT, OutT], Generic[ModelT, OutT]
):
    """``GET /`` and ``GET /{pk}``."""


class CRUDController(
    ListMixin[ModelT, OutT],
    RetrieveMixin[ModelT, OutT],
    CreateMixin[ModelT, OutT, InT],
    UpdateMixin[ModelT, OutT, InT],
    DestroyMixin[ModelT],
    Generic[ModelT, OutT, InT],
):
    """List, retrieve, create, update, partial update and destroy."""


__all__ += ["IN_SCHEMA", "OUT_SCHEMA", "PatchData", "controller_model"]
