"""Standard Webhooks signatures (https://www.standardwebhooks.com)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from typing import Final

__all__ = [
    "InvalidSignature",
    "generate_secret",
    "sign",
    "signature_headers",
    "verify_signature",
]

SECRET_PREFIX: Final = "whsec_"


class InvalidSignature(Exception):
    """The webhook request is not authentic, or too old."""


def generate_secret() -> str:
    """A new signing secret: ``whsec_<base64 of 24 random bytes>``."""
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(24)).decode()


def _key(secret: str) -> bytes:
    raw = secret.removeprefix(SECRET_PREFIX)
    try:
        return base64.b64decode(raw, validate=True)
    except ValueError:
        return raw.encode()


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """``v1,<base64 HMAC-SHA256 of "id.timestamp.body">``."""
    content = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(_key(secret), content, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def signature_headers(
    secret: str, message_id: str, body: bytes, *, timestamp: int | None = None
) -> dict[str, str]:
    """The ``webhook-id``, ``webhook-timestamp`` and ``webhook-signature`` headers.

    :param secret: The endpoint secret (``whsec_...``).
    :param message_id: Unique message id, the same across retries.
    :param body: The exact bytes that are sent.
    :param timestamp: Unix time of the attempt (default: now).
    """
    stamp = int(time.time()) if timestamp is None else timestamp
    return {
        "webhook-id": message_id,
        "webhook-timestamp": str(stamp),
        "webhook-signature": sign(secret, message_id, stamp, body),
    }


def verify_signature(
    secrets_: str | list[str],
    headers: Mapping[str, str],
    body: bytes,
    *,
    tolerance: int = 300,
    now: float | None = None,
) -> None:
    """Raise ``InvalidSignature`` unless ``body`` was signed by one of ``secrets_``.

    For receivers (including Django views: ``verify_signature(secret, request.headers,
    request.body)``). Several secrets allow rotation; ``tolerance`` (seconds) rejects
    replayed old requests.
    :param secrets_: The endpoint secret, or several during a rotation.
    :param headers: Request headers (any case); ``request.headers`` works.
    :param body: The raw request body (``request.body``), not re-serialized JSON.
    :param tolerance: Maximum age of the timestamp, in seconds.
    :param now: The current Unix time (tests).
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    message_id = lowered.get("webhook-id")
    stamp = lowered.get("webhook-timestamp")
    signatures = lowered.get("webhook-signature")
    if not message_id or not stamp or not signatures:
        raise InvalidSignature("missing webhook headers")
    try:
        timestamp = int(stamp)
    except ValueError as exc:
        raise InvalidSignature("invalid timestamp") from exc
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance:
        raise InvalidSignature("timestamp outside the tolerance")
    candidates = [secrets_] if isinstance(secrets_, str) else secrets_
    offered = signatures.split()
    for secret in candidates:
        expected = sign(secret, message_id, timestamp, body)
        if any(hmac.compare_digest(expected, item) for item in offered):
            return
    raise InvalidSignature("no matching signature")
