"""One row per (permission, object, user or group)."""

from __future__ import annotations

import django
from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

USER_XOR_GROUP = Q(user__isnull=False, group__isnull=True) | Q(
    user__isnull=True, group__isnull=False
)


def check_constraint(condition: Q, name: str) -> models.CheckConstraint:
    """``check=`` became ``condition=`` in Django 5.1, and ``check=`` was removed in 6.0."""
    if django.VERSION >= (5, 1):
        return models.CheckConstraint(condition=condition, name=name)
    return models.CheckConstraint(check=condition, name=name)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]


class ObjectGrant(models.Model):
    """``permission`` granted to ``user`` or ``group`` on one object."""

    permission: models.ForeignKey[Permission, Permission] = models.ForeignKey(
        Permission, on_delete=models.CASCADE
    )
    content_type: models.ForeignKey[ContentType, ContentType] = models.ForeignKey(
        ContentType, on_delete=models.CASCADE
    )
    object_pk: models.CharField[str, str] = models.CharField(max_length=255)
    user: models.ForeignKey[models.Model | None, models.Model | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE
    )
    group: models.ForeignKey[Group | None, Group | None] = models.ForeignKey(
        Group, null=True, blank=True, on_delete=models.CASCADE
    )
    created: models.DateTimeField[object, object] = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            check_constraint(USER_XOR_GROUP, "ninja_devx_grant_user_xor_group"),
            models.UniqueConstraint(
                fields=["permission", "content_type", "object_pk", "user"],
                condition=Q(user__isnull=False),
                name="ninja_devx_grant_unique_user",
            ),
            models.UniqueConstraint(
                fields=["permission", "content_type", "object_pk", "group"],
                condition=Q(group__isnull=False),
                name="ninja_devx_grant_unique_group",
            ),
        )
        indexes = (models.Index(fields=["content_type", "object_pk"]),)

    def __str__(self) -> str:
        return f"grant {self.pk} on {self.object_pk}"
