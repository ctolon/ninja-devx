"""Model persistence: re-exports from ``ninja_devx.layers.persistence``, plus ``changed_fields``."""

from __future__ import annotations

from collections.abc import Mapping

from django.db.models import Model

from ..layers.persistence import model_field, save_instance, unknown_fields, validation_failed

__all__ = ["changed_fields", "model_field", "save_instance", "unknown_fields", "validation_failed"]


def changed_fields(instance: Model, data: Mapping[str, object]) -> dict[str, tuple[object, object]]:
    """``{field: (old, new)}`` for the keys in ``data`` that differ from ``instance``.

    Call before writing ``data`` to ``instance``: ``old`` is read from ``instance`` as it
    stands, ``new`` is the incoming value. Foreign keys are compared by primary key;
    many-to-many fields are not compared (call this before ``.set()`` has any effect anyway).

    :param instance: The object about to be updated.
    :param data: The fields being written.
    """
    model = type(instance)
    changes: dict[str, tuple[object, object]] = {}
    for name, new in data.items():
        field = model_field(model, name)
        if field is None or field.many_to_many:
            continue
        if field.is_relation:
            old: object = getattr(instance, field.attname, None)
            candidate: object = new.pk if isinstance(new, Model) else new
        else:
            old = getattr(instance, field.name, None)
            candidate = new
        if old != candidate:
            changes[name] = (old, candidate)
    return changes
