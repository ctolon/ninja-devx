"""Authenticated, database-backed replay of completed controller responses."""

from .policy import idempotent

__all__ = ["idempotent"]
