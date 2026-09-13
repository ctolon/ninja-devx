"""Encrypting webhook signing secrets at rest.

Signing needs the raw secret, so it cannot be hashed like an API key. With
``NINJA_DEVX["WEBHOOK_SECRET_KEYS"]`` set (``pip install "ninja-devx[crypto]"``), secrets
are stored as ``fernet:<token>``; a database dump alone no longer lets anyone forge
webhooks. Generate a key with ``manage.py devx_webhooks generate-key``.

Rotation: put the new key first, keep the old ones after it, and run
``manage.py devx_webhooks encrypt-secrets`` to re-encrypt with the new key.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Final, Protocol, cast

from django.core.exceptions import ImproperlyConfigured

from ...configuration.settings import get_settings

__all__ = ["decrypt_secret", "encrypt_secret", "generate_key", "is_encrypted", "reencrypt_secret"]

PREFIX: Final = "fernet:"


class _MultiFernet(Protocol):
    def encrypt(self, data: bytes) -> bytes: ...

    def decrypt(self, token: bytes) -> bytes: ...

    def rotate(self, token: bytes) -> bytes: ...


def _cipher() -> _MultiFernet | None:
    keys = get_settings().webhook_secret_keys
    if not keys:
        return None
    try:
        fernet = importlib.import_module("cryptography.fernet")
    except ImportError as exc:
        raise ImproperlyConfigured(
            'WEBHOOK_SECRET_KEYS needs cryptography: pip install "ninja-devx[crypto]"'
        ) from exc
    make_fernet = cast("Callable[[bytes], object]", fernet.Fernet)
    make_multi = cast("Callable[[list[object]], _MultiFernet]", fernet.MultiFernet)
    return make_multi([make_fernet(key.encode()) for key in keys])


def is_encrypted(stored: str) -> bool:
    return stored.startswith(PREFIX)


def encrypt_secret(raw: str) -> str:
    """The value to store for ``raw``: encrypted when keys are configured.

    :param raw: The signing secret (``whsec_...``).
    """
    cipher = _cipher()
    if cipher is None:
        return raw
    return PREFIX + cipher.encrypt(raw.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """The raw secret from a stored value (plain values are returned as they are).

    :param stored: The database value.
    """
    if not is_encrypted(stored):
        return stored
    cipher = _cipher()
    if cipher is None:
        raise ImproperlyConfigured("Webhook secrets are encrypted but WEBHOOK_SECRET_KEYS is empty")
    return cipher.decrypt(stored.removeprefix(PREFIX).encode()).decode()


def reencrypt_secret(stored: str) -> str:
    """``stored`` encrypted with the first key (plain values get encrypted).

    :param stored: The database value.
    """
    cipher = _cipher()
    if cipher is None:
        raise ImproperlyConfigured("Set WEBHOOK_SECRET_KEYS before encrypting secrets")
    if not is_encrypted(stored):
        return encrypt_secret(stored)
    return PREFIX + cipher.rotate(stored.removeprefix(PREFIX).encode()).decode()


def generate_key() -> str:
    """A new Fernet key for ``WEBHOOK_SECRET_KEYS``."""
    fernet = importlib.import_module("cryptography.fernet")
    key: object = fernet.Fernet.generate_key()
    return key.decode() if isinstance(key, bytes) else str(key)
