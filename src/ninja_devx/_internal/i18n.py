"""Translation helpers shared by the package (catalog: ``ninja_devx/locale``)."""

from __future__ import annotations

from django.db.models import Model
from django.utils.text import capfirst
from django.utils.translation import gettext as _

__all__ = ["not_found"]


def not_found(model: type[Model]) -> str:
    """``"Article not found"``, with the model's (translated) verbose name."""
    return _("%(name)s not found") % {"name": capfirst(str(model._meta.verbose_name))}
