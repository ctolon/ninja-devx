from __future__ import annotations

import inspect
import sys
from collections.abc import Callable

from django.db import models

__all__ = ["annotation_sources", "has_db_default", "is_composite_primary_key", "signature"]


_COMPOSITE_PRIMARY_KEY: type[object] | None = getattr(models, "CompositePrimaryKey", None)


def has_db_default(field: models.Field[object, object]) -> bool:
    """Whether ``field`` declares ``db_default`` (Django 5.0+)."""
    return getattr(field, "db_default", models.NOT_PROVIDED) is not models.NOT_PROVIDED


def is_composite_primary_key(field: object) -> bool:
    """Whether ``field`` is a ``CompositePrimaryKey`` (Django 5.2+)."""
    return _COMPOSITE_PRIMARY_KEY is not None and isinstance(field, _COMPOSITE_PRIMARY_KEY)


if sys.version_info >= (3, 14):  # pragma: no cover - version specific
    from annotationlib import Format

    def signature(obj: Callable[..., object]) -> inspect.Signature:
        """``inspect.signature`` that tolerates annotations referring to undefined names."""
        return inspect.signature(obj, annotation_format=Format.FORWARDREF)

    def annotation_sources(obj: Callable[..., object]) -> dict[str, str]:
        """The source text of each annotation of ``obj``, without evaluating any."""
        return inspect.get_annotations(obj, format=Format.STRING)

else:  # pragma: no cover - version specific

    def signature(obj: Callable[..., object]) -> inspect.Signature:
        """``inspect.signature`` (annotations are never evaluated before Python 3.14)."""
        return inspect.signature(obj)

    def annotation_sources(obj: Callable[..., object]) -> dict[str, str]:
        """The source text of each string annotation of ``obj`` (others already evaluated)."""
        return {
            name: value
            for name, value in inspect.get_annotations(obj).items()
            if isinstance(value, str)
        }
