"""Object-permission backends backed by ``ObjectGrant``.

``GrantBackend`` is a Django authentication backend answering object-level ``has_perm``.
``GrantsBackend`` implements the ``ninja_devx.security.object_permissions`` backend protocol
and is registered by ``GrantsConfig.ready`` so core does not import this app.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypeVar, cast
from uuid import UUID

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models import CharField, Exists, Model, OuterRef, Q, QuerySet
from django.db.models.functions import Cast

from ...security.object_permissions import Grant
from .models import ObjectGrant

__all__ = ["GrantBackend", "GrantsBackend", "object_permissions_of"]

_CACHE_ATTR = "_ninja_devx_grant_cache"

ModelT = TypeVar("ModelT", bound=Model)


def _is_superuser(user: object) -> bool:
    return bool(getattr(user, "is_active", False) and getattr(user, "is_superuser", False))


def _split(perm: str) -> tuple[str, str]:
    app_label, _, codename = perm.partition(".")
    if not codename:
        raise ValueError(f"Permission {perm!r} must look like 'app_label.codename'")
    return app_label, codename


def _forget(holder: object) -> None:
    """Drop this holder's object-permission cache after a change."""
    state: dict[str, object] = getattr(holder, "__dict__", {})
    state.pop(_CACHE_ATTR, None)


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


def object_permissions_of(user: object, obj: Model) -> set[str]:
    """``{"app_label.codename", ...}`` granted to ``user`` (directly or via groups) on ``obj``.

    Cached on the user object for its lifetime (usually one request), like Django's own
    permission caches.
    """
    if not getattr(user, "is_active", False) or isinstance(user, AnonymousUser):
        return set()
    content_type = ContentType.objects.get_for_model(obj)
    key = (content_type.pk, str(obj.pk))
    cache: dict[tuple[int, str], set[str]] = user.__dict__.setdefault(_CACHE_ATTR, {})
    if key not in cache:
        rows = (
            ObjectGrant.objects.filter(content_type=content_type, object_pk=str(obj.pk))
            .filter(Q(user=user) | Q(group__user=user))
            .values_list("permission__content_type__app_label", "permission__codename")
        )
        cache[key] = {f"{app_label}.{codename}" for app_label, codename in rows}
    return cache[key]


class GrantBackend:
    """Add to ``AUTHENTICATION_BACKENDS`` next to ``ModelBackend``; it never authenticates."""

    def authenticate(self, request: object, **credentials: object) -> None:
        return None

    def has_perm(
        self, user_obj: AbstractBaseUser | AnonymousUser, perm: str, obj: object = None
    ) -> bool:
        if not isinstance(obj, Model):
            return False  # model-level permissions are ModelBackend's job
        return perm in object_permissions_of(user_obj, obj)

    def get_all_permissions(
        self, user_obj: AbstractBaseUser | AnonymousUser, obj: object = None
    ) -> set[str]:
        return object_permissions_of(user_obj, obj) if isinstance(obj, Model) else set()


class GrantsBackend:
    """Object permission backend stored in ``ninja_devx.contrib.grants.ObjectGrant``."""

    def has_perm(self, user: object, perm: str, obj: Model, /) -> bool:
        return _is_superuser(user) or perm in object_permissions_of(user, obj)

    def filter_queryset(
        self, user: object, perms: Sequence[str], queryset: QuerySet[ModelT], /
    ) -> QuerySet[ModelT]:
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
        rows = ObjectGrant.objects.filter(
            content_type=ContentType.objects.get_for_model(obj), object_pk=str(obj.pk)
        ).values_list(
            "user_id",
            "group_id",
            "permission__content_type__app_label",
            "permission__codename",
        )
        return _collect(rows)
