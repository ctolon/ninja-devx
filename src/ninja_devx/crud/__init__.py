"""Generic, typed CRUD controllers for Django models (sync or async from one source)."""

from ..layers.repository import ModelRepository, Repository
from ..layers.services import ModelService
from ..serialization.schemas import Patch, PatchData
from .aggregates import AggregateMixin
from .annotations import Filters, Instance, Locked, Lookup, Ordering, OrderingSchema
from .async_controllers import AsyncCRUDController, AsyncReadOnlyModelController
from .auto import (
    AsyncAutoCRUDController,
    AsyncAutoReadOnlyController,
    AutoCRUDController,
    AutoReadOnlyController,
    model_schemas,
)
from .bulk import (
    BulkCreateMixin,
    BulkDelete,
    BulkDestroyMixin,
    BulkErrorDetail,
    BulkPatch,
    BulkResultOut,
    BulkUpdateMixin,
)
from .controllers import (
    CreateHooks,
    CreateMixin,
    CRUDController,
    DestroyMixin,
    ListConfig,
    ListMixin,
    ModelController,
    ReadOnlyModelController,
    RetrieveMixin,
    UpdateMixin,
)
from .filters import FilterFields
from .meta import ChoiceOut, ControllerMeta, FieldMeta, MetaMixin
from .nested import Parent, get_parent
from .nested_writes import Nested, NestedWritesMixin
from .optimization import ExpandRule, optimize_queryset, related_lookups, requires_related
from .pagination import CursorPagination, LimitOffsetPagination
from .persistence import changed_fields, save_instance
from .search import IContainsSearch, PostgresSearch, SearchBackend
from .sharing import GrantIn, GrantOut, ObjectSharingMixin, RevokeIn
from .soft_delete import SoftDelete, SoftDeleteMixin, soft_delete_unique
from .transitions import InvalidTransition, Transition, TransitionsMixin

__all__ = [
    "AggregateMixin",
    "AsyncAutoCRUDController",
    "AsyncAutoReadOnlyController",
    "AsyncCRUDController",
    "AsyncReadOnlyModelController",
    "AutoCRUDController",
    "AutoReadOnlyController",
    "BulkCreateMixin",
    "BulkDelete",
    "BulkDestroyMixin",
    "BulkErrorDetail",
    "BulkPatch",
    "BulkResultOut",
    "BulkUpdateMixin",
    "CRUDController",
    "ChoiceOut",
    "ControllerMeta",
    "CreateHooks",
    "CreateMixin",
    "CursorPagination",
    "DestroyMixin",
    "ExpandRule",
    "FieldMeta",
    "FilterFields",
    "Filters",
    "GrantIn",
    "GrantOut",
    "IContainsSearch",
    "Instance",
    "InvalidTransition",
    "LimitOffsetPagination",
    "ListConfig",
    "ListMixin",
    "Locked",
    "Lookup",
    "MetaMixin",
    "ModelController",
    "ModelRepository",
    "ModelService",
    "Nested",
    "NestedWritesMixin",
    "ObjectSharingMixin",
    "Ordering",
    "OrderingSchema",
    "Parent",
    "Patch",
    "PatchData",
    "PostgresSearch",
    "ReadOnlyModelController",
    "Repository",
    "RetrieveMixin",
    "RevokeIn",
    "SearchBackend",
    "SoftDelete",
    "SoftDeleteMixin",
    "Transition",
    "TransitionsMixin",
    "UpdateMixin",
    "changed_fields",
    "get_parent",
    "model_schemas",
    "optimize_queryset",
    "related_lookups",
    "requires_related",
    "save_instance",
    "soft_delete_unique",
]
