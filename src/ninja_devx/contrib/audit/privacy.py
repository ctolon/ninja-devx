"""Explicit storage policy for audit changes and metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class AuditPrivacy:
    """Allowlist business fields and redact credential values before persistence.

    Field names are exact; metadata credential keys are matched case-insensitively.
    Use an allowlist for models containing personal or application-specific secrets.
    """

    fields: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ("password", "hashed_secret", "private_key")
    redact: tuple[str, ...] = (
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "authorization",
    )
    metadata_fields: tuple[str, ...] | None = None
    object_repr: bool = False
    """Opt in to calling the model's potentially sensitive ``__str__``."""

    def changes(self, changes: Mapping[str, object]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in changes.items():
            if key in self.exclude or (self.fields is not None and key not in self.fields):
                continue
            if key in self.redact:
                if isinstance(value, list | tuple):
                    result[key] = [
                        None if v is None else "***" for v in cast("list[object]", value)
                    ]
                else:
                    result[key] = "***"
            else:
                result[key] = value
        return result

    def metadata(self, metadata: Mapping[str, object]) -> dict[str, object]:
        selected = {
            key: value
            for key, value in metadata.items()
            if self.metadata_fields is None or key in self.metadata_fields
        }
        return cast("dict[str, object]", self._metadata_value(selected))

    def _metadata_value(self, value: object) -> object:
        if isinstance(value, Mapping):
            sensitive = {name.casefold() for name in (*self.exclude, *self.redact)}
            return {
                key: "***" if key.casefold() in sensitive else self._metadata_value(child)
                for key, child in cast("Mapping[str, object]", value).items()
            }
        if isinstance(value, list | tuple):
            return [self._metadata_value(child) for child in cast("list[object]", value)]
        return value
