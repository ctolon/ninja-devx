"""Generic, typed CRUD controllers for Django models (sync or async from one source)."""

from ..layers.repository import ModelRepository, Repository
from ..layers.services import ModelService
from ..serialization.schemas import Patch, PatchData
from .annotations import Filters, Instance, Locked, Lookup, Ordering, OrderingSchema
from .async_controllers import AsyncCRUDController, AsyncReadOnlyModelController
from .auto import (
    AsyncAutoCRUDController,
    AsyncAutoReadOnlyController,
    AutoCRUDController,
    AutoReadOnlyController,
    model_schemas,
)
from .bulk import BulkCreateMixin, BulkDelete, BulkDestroyMixin, BulkPatch, BulkUpdateMixin
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
from .nested import Parent, get_parent
from .optimization import optimize_queryset, related_lookups
from .pagination import CursorPagination, LimitOffsetPagination
from .persistence import save_instance
from .sharing import GrantIn, GrantOut, ObjectSharingMixin, RevokeIn
from .soft_delete import SoftDelete, SoftDeleteMixin

__all__ = [
    "AsyncAutoCRUDController",
    "AsyncAutoReadOnlyController",
    "AsyncCRUDController",
    "AsyncReadOnlyModelController",
    "AutoCRUDController",
    "AutoReadOnlyController",
    "BulkCreateMixin",
    "BulkDelete",
    "BulkDestroyMixin",
    "BulkPatch",
    "BulkUpdateMixin",
    "CRUDController",
    "CreateHooks",
    "CreateMixin",
    "CursorPagination",
    "DestroyMixin",
    "FilterFields",
    "Filters",
    "GrantIn",
    "GrantOut",
    "Instance",
    "LimitOffsetPagination",
    "ListConfig",
    "ListMixin",
    "Locked",
    "Lookup",
    "ModelController",
    "ModelRepository",
    "ModelService",
    "ObjectSharingMixin",
    "Ordering",
    "OrderingSchema",
    "Parent",
    "Patch",
    "PatchData",
    "ReadOnlyModelController",
    "Repository",
    "RetrieveMixin",
    "RevokeIn",
    "SoftDelete",
    "SoftDeleteMixin",
    "UpdateMixin",
    "get_parent",
    "model_schemas",
    "optimize_queryset",
    "related_lookups",
    "save_instance",
]
