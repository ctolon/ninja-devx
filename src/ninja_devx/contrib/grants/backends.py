"""A Django authentication backend answering object-level ``has_perm`` from ``ObjectGrant``."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model, Q

from .models import ObjectGrant

__all__ = ["GrantBackend", "object_permissions_of"]

_CACHE_ATTR = "_ninja_devx_grant_cache"


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
