"""Writing validated data to Django models, without any web dependency."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeVar

from django.core.exceptions import NON_FIELD_ERRORS, FieldDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, router, transaction
from django.utils.translation import gettext as _

from .errors import ValidationFailed

__all__ = ["model_field", "save_instance", "unknown_fields", "validation_failed"]

ModelT = TypeVar("ModelT", bound=models.Model)


def model_field(model: type[models.Model], name: str) -> models.Field[object, object] | None:
    """The concrete or many-to-many field called ``name`` (or whose ``attname`` is ``name``)."""
    options = model._meta
    try:
        field = options.get_field(name)
    except FieldDoesNotExist:
        return next((f for f in options.concrete_fields if f.attname == name), None)
    return field if isinstance(field, models.Field) else None


def unknown_fields(model: type[models.Model], names: Iterable[str]) -> list[str]:
    return [name for name in names if model_field(model, name) is None]


def validation_failed(exc: DjangoValidationError) -> ValidationFailed:
    """A Django ``ValidationError`` as a domain ``ValidationFailed`` (HTTP 422 when mapped)."""
    errors: dict[str, list[str]] = {}
    if hasattr(exc, "error_dict"):
        for field, messages in exc.message_dict.items():
            errors["" if field == NON_FIELD_ERRORS else field] = list(messages)
    else:
        errors[""] = list(exc.messages)
    return ValidationFailed(_("Validation failed."), errors=errors)


def save_instance(
    instance: ModelT,
    data: Mapping[str, object],
    *,
    validate: bool = True,
    using: str | None = None,
) -> ModelT:
    """Assign ``data`` to ``instance``, validate, save and set many-to-many relations.

    Foreign keys accept an instance or a primary key. ``Model.full_clean`` errors are
    raised as ``ValidationFailed``. Runs in one transaction (a savepoint when nested).
    """
    model = type(instance)
    many_to_many: dict[str, object] = {}
    for name, value in data.items():
        field = model_field(model, name)
        if field is None:
            raise ValueError(f"{model.__name__} has no field {name!r}")
        if field.many_to_many:
            many_to_many[field.name] = value
        elif field.is_relation and not isinstance(value, models.Model):
            setattr(instance, field.attname, value)
        else:
            setattr(instance, field.name, value)

    using = using or router.db_for_write(model, instance=instance)
    with transaction.atomic(using=using):
        if validate:
            try:
                instance.full_clean(exclude=list(many_to_many))
            except DjangoValidationError as exc:
                raise validation_failed(exc) from exc
        instance.save(using=using)
        for name, value in many_to_many.items():
            manager: object = getattr(instance, name)
            setter = getattr(manager, "set")  # noqa: B009 - related managers are untyped
            setter(value)
    return instance
