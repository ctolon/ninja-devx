"""Abstract model bases for bookkeeping columns: timestamps, user stamps, soft deletion.

Combine them with a model and the CRUD controllers fill the columns::

    from ninja_devx.models import SoftDeletable, Stamped

    class Post(Stamped, SoftDeletable):
        title = models.CharField(max_length=200)

``created_at``/``updated_at`` are maintained by Django (``auto_now_add``/``auto_now``);
``created_by``/``updated_by`` are set from the request user on create and update, and
``deleted_at``/``deleted_by`` by ``SoftDeleteMixin``. All of them are ``editable=False``,
so generated input schemas, scaffolding and drift checks leave them out.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

__all__ = ["SoftDeletable", "Stamped", "TimeStamped", "UserStamped"]


def _user_reference() -> models.ForeignKey[object, object]:
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )


class TimeStamped(models.Model):
    """``created_at`` (indexed) and ``updated_at``, maintained by Django."""

    created_at: models.DateTimeField[object, object] = models.DateTimeField(
        auto_now_add=True, editable=False, db_index=True
    )
    updated_at: models.DateTimeField[object, object] = models.DateTimeField(
        auto_now=True, editable=False
    )

    class Meta:
        abstract = True


class UserStamped(models.Model):
    """``created_by`` and ``updated_by``, set from the request user by model controllers.

    Both are nullable so anonymous writes and deleted users leave ``None``.
    """

    created_by: models.ForeignKey[object, object] = _user_reference()
    updated_by: models.ForeignKey[object, object] = _user_reference()

    class Meta:
        abstract = True


class Stamped(TimeStamped, UserStamped):
    """Timestamps and user stamps together."""

    class Meta(TimeStamped.Meta, UserStamped.Meta):
        abstract = True


class SoftDeletable(models.Model):
    """``deleted_at``/``deleted_by`` with the defaults ``SoftDeleteMixin`` expects.

    A controller using ``SoftDeleteMixin`` needs no ``soft_delete`` configuration for a
    model built on this base: rows are marked with the time and the requesting user.
    """

    deleted_at: models.DateTimeField[object, object] = models.DateTimeField(
        null=True, blank=True, editable=False, db_index=True
    )
    deleted_by: models.ForeignKey[object, object] = _user_reference()

    class Meta:
        abstract = True
