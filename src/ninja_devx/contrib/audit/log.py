"""Recording audit entries and diffing model snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import ClassVar, Final, Generic, Protocol, cast

from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, router, transaction
from django.http import HttpRequest
from pydantic import BaseModel

from ...crud.controllers import ModelController, ModelT
from ...http.middleware import get_request_id
from ...security.auth import request_user
from .models import AuditEntry
from .privacy import AuditPrivacy

__all__ = ["REDACTED", "AuditMixin", "diff", "record", "redact", "snapshot"]

REDACTED: Final = "***"


def _json(value: object) -> object:
    loaded: object = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
    return loaded


def snapshot(
    instance: models.Model,
    *,
    fields: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
    redact: Sequence[str] = (),
) -> dict[str, object]:
    """JSON-safe values of ``instance``'s concrete fields (foreign keys as their key).

    :param instance: The model instance.
    :param fields: Field names to keep (``None``: all concrete fields).
    :param exclude: Field names to leave out.
    :param redact: Field names whose values become ``"***"``.
    """
    values: dict[str, object] = {}
    for field in instance._meta.concrete_fields:
        if (fields is not None and field.name not in fields) or field.name in exclude:
            continue
        value: object = getattr(instance, field.attname)
        values[field.name] = REDACTED if field.name in redact else _json(value)
    return values


def diff(before: Mapping[str, object], after: Mapping[str, object]) -> dict[str, list[object]]:
    """``{field: [old, new]}`` for the fields whose value changed.

    :param before: A snapshot taken before the change.
    :param after: A snapshot taken after it.
    """
    return {
        name: [before.get(name), after.get(name)]
        for name in dict.fromkeys([*before, *after])
        if before.get(name) != after.get(name)
    }


def redact(changes: Mapping[str, list[object]], fields: Sequence[str]) -> dict[str, list[object]]:
    """Replace the values of ``fields`` by ``"***"`` (a change stays visible).

    :param changes: ``{field: [old, new]}``.
    :param fields: Field names to hide.
    """
    return {
        name: [None if v is None else REDACTED for v in values] if name in fields else values
        for name, values in changes.items()
    }


def _client_ip(request: HttpRequest) -> str | None:
    address: object = request.META.get("REMOTE_ADDR")
    return address if isinstance(address, str) and address else None


def record(
    request: HttpRequest | None,
    action: str,
    obj: models.Model | None = None,
    *,
    object_pk: object = None,
    changes: Mapping[str, object] | None = None,
    metadata: Mapping[str, object] | None = None,
    using: str | None = None,
    privacy: AuditPrivacy | None = None,
) -> AuditEntry:
    """Write an audit entry for ``action`` on ``obj``, attributed to ``request``'s user.

    :param request: The request (actor, request id, method, path, IP); ``None`` for jobs.
    :param action: A short verb: ``"create"``, ``"update"``, ``"delete"``, ``"export"``...
    :param obj: The object acted on, if any.
    :param object_pk: The key when ``obj`` no longer has one (after a delete).
    :param changes: ``{field: [old, new]}``.
    :param metadata: Anything else worth keeping (JSON-serializable).
    :param using: Database alias; defaults to the object database, then the audit write router.
    :param privacy: Storage allowlists/redaction policy; default protects common credential fields.
    """
    policy = privacy or AuditPrivacy()
    user = request_user(request) if request is not None else None
    key: object = object_pk if object_pk is not None else (obj.pk if obj is not None else None)
    alias = (
        using
        or (obj._state.db if obj is not None else None)
        or router.db_for_write(AuditEntry, instance=obj)
    )
    content_type = (
        ContentType.objects.db_manager(alias).get_for_model(obj) if obj is not None else None
    )
    return AuditEntry.objects.using(alias).create(
        action=action,
        actor_id=getattr(user, "pk", None),
        actor_label=str(user) if user is not None else "",
        content_type_id=content_type.pk if content_type is not None else None,
        object_pk="" if key is None else str(key),
        object_repr=(
            (str(obj) if policy.object_repr else f"{obj._meta.label}:{key}")[:200]
            if obj is not None
            else ""
        ),
        changes=_json(policy.changes(changes or {})),
        metadata=_json(policy.metadata(metadata or {})),
        request_id=(get_request_id(request) or "")[:64] if request is not None else "",
        method=request.method or "" if request is not None else "",
        path=request.path[:500] if request is not None else "",
        ip_address=_client_ip(request) if request is not None else None,
    )


class _Writes(Protocol[ModelT]):
    def perform_create(self, request: HttpRequest, payload: BaseModel) -> ModelT: ...

    def perform_update(
        self, request: HttpRequest, instance: ModelT, data: Mapping[str, object]
    ) -> ModelT: ...

    def perform_destroy(self, request: HttpRequest, instance: ModelT) -> None: ...


class AuditMixin(ModelController[ModelT], Generic[ModelT]):
    """Record creates, updates and deletes of a model controller (bulk ones too).

    List it first: ``class Invoices(AuditMixin[Invoice], CRUDController[...])``. Your own
    ``perform_*`` overrides on the controller are audited as well.
    """

    __devx_wraps__: ClassVar[frozenset[str]] = frozenset(
        {"perform_create", "perform_update", "perform_destroy"}
    )
    audit_privacy: ClassVar[AuditPrivacy] = AuditPrivacy()
    """Storage allowlists, credential redaction and safe object labels."""
    audit_fields: ClassVar[Sequence[str] | None] = None
    """Fields kept in snapshots and diffs (``None``: every concrete field)."""
    audit_exclude: ClassVar[Sequence[str]] = ("password",)
    """Fields never recorded."""
    audit_redact: ClassVar[Sequence[str]] = ()
    """Fields recorded as ``"***"``: a change is visible, the value is not."""

    def audit_metadata(self, request: HttpRequest) -> Mapping[str, object] | None:
        """Extra data stored with every entry (override: ``{"tenant": ...}``)."""
        return None

    def audit_snapshot(self, instance: ModelT) -> dict[str, object]:
        cls = type(self)
        return snapshot(instance, fields=cls.audit_fields, exclude=cls.audit_exclude)

    def audit(
        self,
        request: HttpRequest,
        action: str,
        instance: ModelT,
        *,
        changes: Mapping[str, list[object]],
        object_pk: object = None,
    ) -> AuditEntry:
        """Write one entry; override to enrich or filter what is recorded."""
        cls = type(self)
        metadata = self.audit_metadata(request)
        return record(
            request,
            action,
            instance,
            object_pk=object_pk,
            changes=redact(changes, cls.audit_redact),
            metadata=metadata,
            privacy=cls.audit_privacy,
        )

    def _writes(self) -> _Writes[ModelT]:
        return cast("_Writes[ModelT]", super())

    def perform_create(self, request: HttpRequest, payload: BaseModel) -> ModelT:
        with transaction.atomic(using=self.write_database(request)):
            instance = self._writes().perform_create(request, payload)
            after = self.audit_snapshot(instance)
            self.audit(
                request, "create", instance, changes={k: [None, v] for k, v in after.items()}
            )
        return instance

    def perform_update(
        self, request: HttpRequest, instance: ModelT, data: Mapping[str, object]
    ) -> ModelT:
        before = self.audit_snapshot(instance)
        with transaction.atomic(using=self.write_database(request)):
            updated = self._writes().perform_update(request, instance, data)
            self.audit(
                request, "update", updated, changes=diff(before, self.audit_snapshot(updated))
            )
        return updated

    def perform_destroy(self, request: HttpRequest, instance: ModelT) -> None:
        before = self.audit_snapshot(instance)
        pk: object = instance.pk
        with transaction.atomic(using=self.write_database(request)):
            self._writes().perform_destroy(request, instance)
            self.audit(
                request,
                "delete",
                instance,
                object_pk=pk,
                changes={k: [v, None] for k, v in before.items()},
            )
