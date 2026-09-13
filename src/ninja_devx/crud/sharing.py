"""Share objects over the API: list, grant and revoke object permissions per user or group.

::

    class DocumentController(ObjectSharingMixin[Document], CRUDController[Document, Out, In]):
        object_permissions = ObjectPermissions()
        shareable_permissions = ("view", "change")     # codename prefixes clients may grant

Routes (rename or disable them with ``routes``):

- ``GET /{pk}/permissions`` — who holds what on the object;
- ``PUT /{pk}/permissions`` — set a user's or group's permissions (replaces them);
- ``POST /{pk}/permissions/revoke`` — remove a user's or group's permissions.

Managing sharing requires ``sharing_permission`` on the object (``change`` by default).
"""

from __future__ import annotations

from typing import ClassVar, Generic
from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import Schema
from ninja.errors import HttpError
from pydantic import Field, model_validator

from ..routing.operations import get, post, put
from ..security.auth import request_user
from ..security.object_permissions import Grant, assign_perm, get_backend, grants_for, remove_perm
from ..security.permissions import IsAuthenticated
from .annotations import Lookup
from .controllers import ModelController, ModelT

__all__ = ["GrantIn", "GrantOut", "ObjectSharingMixin", "RevokeIn"]


class GrantOut(Schema):
    user_id: int | str | UUID | None = Field(None, description="Holder user, or null for a group.")
    group_id: int | None = Field(None, description="Holder group, or null for a user.")
    permissions: list[str] = Field(description="Full permission names (`app_label.codename`).")


class _Holder(Schema):
    user_id: int | str | UUID | None = None
    group_id: int | None = None

    @model_validator(mode="after")
    def _one_holder(self) -> _Holder:
        if (self.user_id is None) == (self.group_id is None):
            raise ValueError("give exactly one of user_id and group_id")
        return self


class GrantIn(_Holder):
    permissions: list[str] = Field(
        description="Codenames without the model suffix (`view`, `change`) or full names."
    )


class RevokeIn(_Holder):
    pass


class ObjectSharingMixin(ModelController[ModelT], Generic[ModelT]):
    """Endpoints managing object permissions (grants backend or django-guardian)."""

    shareable_permissions: ClassVar[tuple[str, ...]] = ("view", "change", "delete")
    """Permission actions clients may grant (``view`` means ``<app>.view_<model>``)."""
    sharing_permission: ClassVar[str] = "change"
    """Action the caller needs on the object to see or change its sharing."""

    @get(
        "/{pk}/permissions",
        response=list[GrantOut],
        permissions=[IsAuthenticated()],
        summary="List who can access the object",
    )
    def list_permissions(self, request: HttpRequest, pk: Lookup) -> list[Grant]:
        return grants_for(self._sharing_object(request, pk))

    @put(
        "/{pk}/permissions",
        response=list[GrantOut],
        permissions=[IsAuthenticated()],
        summary="Set a user's or group's permissions on the object",
    )
    def set_permissions(self, request: HttpRequest, pk: Lookup, payload: GrantIn) -> list[Grant]:
        obj = self._sharing_object(request, pk)
        wanted = {self._full_permission(name) for name in payload.permissions}
        holder = self._holder(payload)
        self.validate_holder(request, obj, holder)
        for perm in self._all_shareable():
            if perm in wanted:
                assign_perm(perm, holder, obj)
            else:
                remove_perm(perm, holder, obj)
        return grants_for(obj)

    @post(
        "/{pk}/permissions/revoke",
        response=list[GrantOut],
        permissions=[IsAuthenticated()],
        summary="Remove a user's or group's permissions on the object",
    )
    def revoke_permissions(
        self, request: HttpRequest, pk: Lookup, payload: RevokeIn
    ) -> list[Grant]:
        obj = self._sharing_object(request, pk)
        holder = self._holder(payload)
        for perm in self._all_shareable():
            remove_perm(perm, holder, obj)
        return grants_for(obj)

    def validate_holder(self, request: HttpRequest, obj: ModelT, holder: object) -> None:
        """Override to restrict who objects may be shared with (raise ``HttpError(422)``),
        e.g. members of the same workspace. Revoking is always allowed."""

    # --- helpers ------------------------------------------------------------------------

    def _sharing_object(self, request: HttpRequest, lookup: object) -> ModelT:
        """The object (tenant, parent and view scoping apply: 404), then the sharing check (403)."""
        obj = self.get_object_from(request, self.scoped_queryset(request), lookup)
        perm = self._permission(self.sharing_permission)
        if not get_backend().has_perm(request_user(request), perm, obj):
            raise HttpError(403, _("You cannot manage who can access this object."))
        return obj

    def _permission(self, action: str) -> str:
        options = self.get_model()._meta
        return f"{options.app_label}.{action}_{options.model_name}"

    def _full_permission(self, name: str) -> str:
        """``view`` or ``app.view_model`` → ``app.view_model``, if it may be shared."""
        allowed = {self._permission(action): action for action in self.shareable_permissions}
        perm = name if "." in name else self._permission(name)
        if perm not in allowed:
            raise HttpError(
                422,
                _("%(name)s cannot be shared; allowed: %(allowed)s")
                % {"name": name, "allowed": ", ".join(sorted(allowed.values()))},
            )
        return perm

    def _all_shareable(self) -> list[str]:
        return [self._permission(action) for action in self.shareable_permissions]

    @staticmethod
    def _holder(payload: _Holder) -> object:
        try:
            if payload.user_id is not None:
                return get_user_model()._default_manager.get(pk=payload.user_id)
            return Group.objects.get(pk=payload.group_id)
        except (
            get_user_model().DoesNotExist,
            Group.DoesNotExist,
            DjangoValidationError,
            ValueError,
        ) as exc:
            raise HttpError(422, _("Unknown user or group")) from exc
