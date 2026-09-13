"""Compatibility re-exports: model persistence lives in ``ninja_devx.layers.persistence``."""

from ..layers.persistence import model_field, save_instance, unknown_fields, validation_failed

__all__ = ["model_field", "save_instance", "unknown_fields", "validation_failed"]
