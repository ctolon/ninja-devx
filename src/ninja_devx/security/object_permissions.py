"""Object-level permissions: per-row grants, checked on objects and applied to lists.

The model follows django-guardian and DRF's ``DjangoObjectPermissions``: permissions are
Django's ``app_label.codename`` strings (``blog.change_post``), granted to users or groups
on single objects. A backend stores and checks them:

====================  ==================================================================
``GrantsBackend``     ``ninja_devx.contrib.grants``: no extra dependency
``GuardianBackend``   django-guardian (``pip install ninja-devx[guardian]``)
``DjangoBackend``     any authentication backend's ``user.has_perm(perm, obj)``; checks
                      only, lists cannot be filtered
====================  ==================================================================

``NINJA_DEVX["OBJECT_PERMISSION_BACKEND"]`` picks one (an instance or import path). By
default: grants when ``ninja_devx.contrib.grants`` is installed, else guardian when
installed, else ``DjangoBackend``.

::

    class DocumentController(CRUDController[Document, DocumentOut, DocumentIn]):
        object_permissions = ObjectPermissions()      # 404 without view, 403 without change

    assign_perm("docs.change_document", bob, document)
    get_objects_for_user(bob, "docs.view_document", Document.objects.all())
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Protocol, TypeVar, cast, runtime_checkable
from uuid import UUID

from asgiref.sync import sync_to_async
from django.apps import apps
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.db.models import CharField, Exists, Model, OuterRef, Q, QuerySet
from django.db.models.functions import Cast
from django.http import Http404, HttpRequest
from django.utils.module_loading import import_string
from django.utils.translation import gettext_noop

from .._internal.i18n import not_found
from ..configuration.settings import SETTINGS_NAME, get_settings
from .auth import arequest_user, request_user
from .permissions import BasePermission

__all__ = [
    "DEFAULT_PERMS_MAP",
    "DjangoBackend",
    "Grant",
    "GrantsBackend",
    "GuardianBackend",
    "ObjectPermissionBackend",
    "ObjectPermissionStore",
    "ObjectPermissions",
    "assign_perm",
    "get_backend",
    "get_objects_for_user",
    "get_perms",
    "grants_for",
    "remove_perm",
]

ModelT = TypeVar("ModelT", bound=Model)

DEFAULT_PERMS_MAP: Final[Mapping[str, Sequence[str]]] = MappingProxyType(
    {
        "GET": ("%(app_label)s.view_%(model_name)s",),
        "HEAD": ("%(app_label)s.view_%(model_name)s",),
        "OPTIONS": (),
        "POST": ("%(app_label)s.add_%(model_name)s",),
        "PUT": ("%(app_label)s.change_%(model_name)s",),
        "PATCH": ("%(app_label)s.change_%(model_name)s",),
        "DELETE": ("%(app_label)s.delete_%(model_name)s",),
    }
)


@dataclass(frozen=True, slots=True)
class Grant:
    """Permissions a user or a group holds on one object."""

    user_id: int | str | UUID | None
    group_id: int | None
    permissions: tuple[str, ...]


@runtime_checkable
class ObjectPermissionBackend(Protocol):
    def has_perm(self, user: object, perm: str, obj: Model, /) -> bool:
        """Whether ``user`` holds ``perm`` on ``obj``."""
        ...

    def filter_queryset(
        self, user: object, perms: Sequence[str], queryset: QuerySet[ModelT], /
    ) -> QuerySet[ModelT]:
        """The objects of ``queryset`` on which ``user`` holds every one of ``perms``."""
        ...


@runtime_checkable
class ObjectPermissionStore(Protocol):
    def assign(self, perm: str, holder: object, obj: Model, /) -> None: ...

    def remove(self, perm: str, holder: object, obj: Model, /) -> None: ...

    def grants(self, obj: Model, /) -> list[Grant]: ...


def _is_superuser(user: object) -> bool:
    return bool(getattr(user, "is_active", False) and getattr(user, "is_superuser", False))


def _split(perm: str) -> tuple[str, str]:
    app_label, _, codename = perm.partition(".")
    if not codename:
        raise ValueError(f"Permission {perm!r} must look like 'app_label.codename'")
    return app_label, codename


class DjangoBackend:
    """``user.has_perm(perm, obj)`` through ``AUTHENTICATION_BACKENDS``; no list filtering."""

    def has_perm(self, user: object, perm: str, obj: Model, /) -> bool:
        check: Callable[[str, Model], bool] | None = getattr(user, "has_perm", None)
        return bool(check and check(perm, obj))

    def filter_queryset(
        self, user: object, perms: Sequence[str], queryset: QuerySet[ModelT], /
    ) -> QuerySet[ModelT]:
        raise ImproperlyConfigured(
            "DjangoBackend cannot filter lists by object permission; install "
            "ninja_devx.contrib.grants or django-guardian, or set filter_lists=False"
        )


class GrantsBackend:
    """Grants stored in ``ninja_devx.contrib.grants.ObjectGrant``."""

    def has_perm(self, user: object, perm: str, obj: Model, /) -> bool:
        from ..contrib.grants.backends import object_permissions_of

        return _is_superuser(user) or perm in object_permissions_of(user, obj)

    def filter_queryset(
        self, user: object, perms: Sequence[str], queryset: QuerySet[ModelT], /
    ) -> QuerySet[ModelT]:
        from django.contrib.contenttypes.models import ContentType

        from ..contrib.grants.models import ObjectGrant

        if _is_superuser(user):
            return queryset
        if not getattr(user, "is_active", False) or getattr(user, "pk", None) is None:
            return queryset.none()
        content_type = ContentType.objects.get_for_model(queryset.model)
        holders = Q(user=user) | Q(group__user=user)
        for perm in perms:
            app_label, codename = _split(perm)
            granted = ObjectGrant.objects.filter(
                holders,
                content_type=content_type,
                object_pk=Cast(OuterRef("pk"), output_field=CharField()),
                permission__codename=codename,
                permission__content_type__app_label=app_label,
            )
            queryset = queryset.filter(Exists(granted))
        return queryset

    def assign(self, perm: str, holder: object, obj: Model, /) -> None:
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType

        from ..contrib.grants.models import ObjectGrant

        app_label, codename = _split(perm)
        permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        content_type = ContentType.objects.get_for_model(obj)
        if isinstance(holder, Group):
            ObjectGrant.objects.get_or_create(
                permission=permission,
                content_type=content_type,
                object_pk=str(obj.pk),
                group=holder,
            )
        else:
            ObjectGrant.objects.get_or_create(
                permission=permission,
                content_type=content_type,
                object_pk=str(obj.pk),
                user=cast("Model", holder),
            )
        _forget(holder)

    def remove(self, perm: str, holder: object, obj: Model, /) -> None:
        from django.contrib.contenttypes.models import ContentType

        from ..contrib.grants.models import ObjectGrant

        app_label, codename = _split(perm)
        target = {"group": holder} if isinstance(holder, Group) else {"user": holder}
        ObjectGrant.objects.filter(
            content_type=ContentType.objects.get_for_model(obj),
            object_pk=str(obj.pk),
            permission__codename=codename,
            permission__content_type__app_label=app_label,
            **target,
        ).delete()
        _forget(holder)

    def grants(self, obj: Model, /) -> list[Grant]:
        from django.contrib.contenttypes.models import ContentType

        from ..contrib.grants.models import ObjectGrant

        rows = ObjectGrant.objects.filter(
            content_type=ContentType.objects.get_for_model(obj), object_pk=str(obj.pk)
        ).values_list(
            "user_id",
            "group_id",
            "permission__content_type__app_label",
            "permission__codename",
        )
        return _collect(rows)


def _guardian(module: str, name: str) -> Callable[..., object]:
    """A django-guardian function, typed loosely on purpose (guardian's own hints use Any)."""
    found: Callable[..., object] = getattr(importlib.import_module(f"guardian.{module}"), name)
    return found


class GuardianBackend:
    """django-guardian's ``ObjectPermissionChecker`` and shortcuts."""

    def has_perm(self, user: object, perm: str, obj: Model, /) -> bool:
        if _is_superuser(user):
            return True
        checker: object = getattr(user, "_ninja_devx_guardian", None)
        if checker is None:
            checker = _guardian("core", "ObjectPermissionChecker")(user)
            setattr(user, "_ninja_devx_guardian", checker)  # noqa: B010 - cached per user object
        _, codename = _split(perm)
        check: Callable[[str, Model], object] = getattr(checker, "has_perm")  # noqa: B009
        return bool(check(codename, obj))

    def filter_queryset(
        self, user: object, perms: Sequence[str], queryset: QuerySet[ModelT], /
    ) -> QuerySet[ModelT]:
        found = _guardian("shortcuts", "get_objects_for_user")(
            user, list(perms), klass=queryset, accept_global_perms=False
        )
        return cast("QuerySet[ModelT]", found)

    def assign(self, perm: str, holder: object, obj: Model, /) -> None:
        _guardian("shortcuts", "assign_perm")(perm, holder, obj)
        _forget(holder)

    def remove(self, perm: str, holder: object, obj: Model, /) -> None:
        _guardian("shortcuts", "remove_perm")(perm, holder, obj)
        _forget(holder)

    def grants(self, obj: Model, /) -> list[Grant]:
        users = cast(
            "Mapping[Model, Sequence[str]]",
            _guardian("shortcuts", "get_users_with_perms")(
                obj, attach_perms=True, with_group_users=False
            ),
        )
        groups = cast(
            "Mapping[Model, Sequence[str]]",
            _guardian("shortcuts", "get_groups_with_perms")(obj, attach_perms=True),
        )
        app_label = obj._meta.app_label
        rows: list[tuple[object, object, str, str]] = []
        for user, codenames in users.items():
            rows += [(user.pk, None, app_label, codename) for codename in codenames]
        for group, codenames in groups.items():
            rows += [(None, group.pk, app_label, codename) for codename in codenames]
        return _collect(rows)


def _collect(rows: Iterable[tuple[object, object, str, str]]) -> list[Grant]:
    by_holder: dict[tuple[object, object], list[str]] = {}
    for user_id, group_id, app_label, codename in rows:
        by_holder.setdefault((user_id, group_id), []).append(f"{app_label}.{codename}")
    return [
        Grant(
            user_id=cast("int | str | UUID | None", user_id),
            group_id=cast("int | None", group_id),
            permissions=tuple(sorted(perms)),
        )
        for (user_id, group_id), perms in by_holder.items()
    ]


def _forget(holder: object) -> None:
    """Drop per-user permission caches after a change (for this user object)."""
    state: dict[str, object] = getattr(holder, "__dict__", {})
    for attribute in ("_ninja_devx_grant_cache", "_ninja_devx_guardian", "_perm_cache"):
        state.pop(attribute, None)


def get_backend() -> ObjectPermissionBackend:
    configured = get_settings().object_permission_backend
    if configured is not None:
        backend: object = import_string(configured)() if isinstance(configured, str) else configured
        if not isinstance(backend, ObjectPermissionBackend):
            raise ImproperlyConfigured(
                f"{SETTINGS_NAME}['OBJECT_PERMISSION_BACKEND'] is not an ObjectPermissionBackend"
            )
        return backend
    if apps.is_installed("ninja_devx.contrib.grants"):
        return GrantsBackend()
    if apps.is_installed("guardian"):
        return GuardianBackend()
    return DjangoBackend()


def _store() -> ObjectPermissionStore:
    backend = get_backend()
    if not isinstance(backend, ObjectPermissionStore):
        raise ImproperlyConfigured(f"{type(backend).__name__} cannot store grants")
    return backend


def assign_perm(perm: str, holder: object, obj: Model) -> None:
    """Grant ``perm`` (``"app_label.codename"``) to a user or group on ``obj``.

    :param perm: Full permission name, ``"app_label.codename"``.
    :param holder: A user or a ``Group``.
    :param obj: The model instance.
    """
    _store().assign(perm, holder, obj)


def remove_perm(perm: str, holder: object, obj: Model) -> None:
    """Revoke ``perm`` from a user or group on ``obj``.

    :param perm: Full permission name, ``"app_label.codename"``.
    :param holder: A user or a ``Group``.
    :param obj: The model instance.
    """
    _store().remove(perm, holder, obj)


def grants_for(obj: Model) -> list[Grant]:
    """Every user and group holding permissions on ``obj``.

    :param obj: The model instance.
    """
    return _store().grants(obj)


def get_perms(user: object, obj: Model, perms: Sequence[str]) -> list[str]:
    """The subset of ``perms`` that ``user`` holds on ``obj``.

    :param user: The user (anonymous users hold nothing).
    :param obj: The model instance.
    :param perms: Full permission names to test.
    """
    backend = get_backend()
    return [perm for perm in perms if backend.has_perm(user, perm, obj)]


def get_objects_for_user(
    user: object, perms: str | Sequence[str], queryset: QuerySet[ModelT]
) -> QuerySet[ModelT]:
    """Objects of ``queryset`` on which ``user`` holds every one of ``perms``.

    :param user: The user.
    :param perms: One or several full permission names, all required.
    :param queryset: The objects to filter.
    """
    wanted = (perms,) if isinstance(perms, str) else tuple(perms)
    return get_backend().filter_queryset(user, wanted, queryset)


@dataclass(frozen=True, slots=True)
class ObjectPermissions(BasePermission[Model]):
    """Per-object Django permissions by HTTP method, like DRF's ``DjangoObjectPermissions``.

    - Detail operations need the object permissions of the method (``view`` for GET,
      ``change`` for PUT/PATCH, ``delete`` for DELETE).
    - A caller who cannot even view the object gets 404, so existence doesn't leak.
    - With ``filter_lists`` (used by ``ModelController.object_permissions``), queries only
      return objects the caller can view.
    - ``model_permissions=True`` also accepts a model-wide permission
      (``user.has_perm("blog.change_post")``) for users with global rights.
    """

    perms_map: Mapping[str, Sequence[str]] = field(default_factory=lambda: dict(DEFAULT_PERMS_MAP))
    """HTTP method → permission templates (``%(app_label)s``, ``%(model_name)s``)."""
    model_permissions: bool = False
    """Accept model-wide Django permissions in addition to object grants."""
    filter_lists: bool = True
    """Restrict querysets to objects the caller can view (needs a filtering backend)."""
    hide_forbidden: bool = True
    """Answer 404 instead of 403 when the caller cannot view the object."""

    message = gettext_noop("You do not have permission to perform this action on this object.")

    def permissions_for(self, method: str, model: type[Model]) -> list[str]:
        options = model._meta
        values = {"app_label": options.app_label, "model_name": options.model_name}
        return [template % values for template in self.perms_map.get(method.upper(), ())]

    def has_permission(self, request: HttpRequest, /) -> bool:
        user = request_user(request)
        return user is not None and bool(getattr(user, "is_authenticated", False))

    async def ahas_permission(self, request: HttpRequest, /) -> bool:
        user = await arequest_user(request)
        return user is not None and bool(getattr(user, "is_authenticated", False))

    def has_object_permission(self, request: HttpRequest, obj: Model, /) -> bool:
        return self._allows(request, request_user(request), obj)

    async def ahas_object_permission(self, request: HttpRequest, obj: Model, /) -> bool:
        user = await arequest_user(request)
        return bool(await sync_to_async(self._allows)(request, user, obj))

    def _allows(self, request: HttpRequest, user: object, obj: Model) -> bool:
        backend = get_backend()
        model = type(obj)
        required = self.permissions_for(request.method or "GET", model)
        if all(self._holds(backend, user, perm, obj) for perm in required):
            return True
        if self.hide_forbidden:
            readable = self.permissions_for("GET", model)
            if not all(self._holds(backend, user, perm, obj) for perm in readable):
                raise Http404(not_found(model))
        return False

    def _holds(self, backend: ObjectPermissionBackend, user: object, perm: str, obj: Model) -> bool:
        if backend.has_perm(user, perm, obj):
            return True
        if self.model_permissions:
            check: Callable[[str], bool] | None = getattr(user, "has_perm", None)
            return bool(check and check(perm))
        return False

    def filter(self, request: HttpRequest, queryset: QuerySet[ModelT]) -> QuerySet[ModelT]:
        """``queryset`` restricted to objects the caller can view."""
        user = request_user(request)
        model = queryset.model
        readable = self.permissions_for("GET", model)
        if self.model_permissions:
            check: Callable[[str], bool] | None = getattr(user, "has_perm", None)
            if check and all(check(perm) for perm in readable):
                return queryset
        return get_backend().filter_queryset(user, readable, queryset)
