"""Faster JSON renderers for Ninja: ``NinjaAPI(renderer=ORJSONRenderer())``.

Serialization of the response body is often the largest per-request cost after the
database. Both renderers accept what Ninja's ``JSONRenderer`` does (datetimes, UUIDs,
``Decimal``, pydantic models) and produce the same JSON, except that datetimes keep their
microseconds (Django's encoder truncates them to milliseconds).

- ``ORJSONRenderer`` needs ``orjson`` (``pip install ninja-devx[orjson]``).
- ``MsgspecRenderer`` needs ``msgspec`` (``pip install ninja-devx[msgspec]``).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import ClassVar

from django.http import HttpRequest
from ninja.renderers import BaseRenderer
from ninja.responses import NinjaJSONEncoder
from pydantic import BaseModel

__all__ = ["MsgspecRenderer", "ORJSONRenderer"]


def _default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)  # Ninja's encoder renders Decimal as a string too
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return NinjaJSONEncoder().default(value)


class ORJSONRenderer(BaseRenderer):
    """JSON with orjson (``ninja-devx[orjson]``): ``NinjaAPI(renderer=ORJSONRenderer())``.

    Output matches Ninja's encoder (``Z`` datetimes, decimals as strings) except that
    datetimes keep microseconds.
    """

    media_type = "application/json"
    options: ClassVar[int | None] = None
    """Extra ``orjson.OPT_*`` flags (``orjson.OPT_INDENT_2``...)."""

    def render(self, request: HttpRequest, data: object, *, response_status: int) -> bytes:
        import orjson

        flags = orjson.OPT_UTC_Z | (type(self).options or 0)  # "Z" like Django's encoder
        dumped: bytes = orjson.dumps(data, default=_default, option=flags)
        return dumped


class MsgspecRenderer(BaseRenderer):
    """JSON with msgspec (``ninja-devx[msgspec]``); same output rules as ``ORJSONRenderer``."""

    media_type = "application/json"

    def __init__(self) -> None:
        import msgspec

        self._encode: Callable[[object], bytes] = msgspec.json.Encoder(enc_hook=_default).encode

    def render(self, request: HttpRequest, data: object, *, response_status: int) -> bytes:
        return self._encode(data)
