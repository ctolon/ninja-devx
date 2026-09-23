"""Exact, atomic fixed-window throttle counting on Redis.

Needs ``pip install ninja-devx[redis]``. Point a throttle's own ``storage=``, or the
project-wide ``NINJA_DEVX["THROTTLE_STORAGE"]``, at an instance::

    # myapp/throttling.py
    from ninja_devx.contrib.redis_throttle import RedisThrottleStorage

    redis_throttle_storage = RedisThrottleStorage(url="redis://cache:6379/1")

    NINJA_DEVX = {"THROTTLE_STORAGE": "myapp.throttling.redis_throttle_storage"}

``INCR`` and a conditional ``EXPIRE`` (``NX``: only on the window's first hit) run in one
``MULTI``/``EXEC`` transaction, so counting is atomic even under concurrent requests, unlike
the default cache-based storage's unprotected ``add`` then ``incr``.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Sequence
from typing import Protocol, cast

__all__ = ["RedisThrottleStorage"]


class _RedisPipeline(Protocol):
    def incr(self, name: str) -> object: ...

    def expire(self, name: str, time: int, *, nx: bool = False) -> object: ...

    def execute(self) -> Sequence[int]: ...


class _RedisClient(Protocol):
    def pipeline(self, transaction: bool = True) -> _RedisPipeline: ...


class RedisThrottleStorage:
    """``ThrottleStorage`` counting fixed windows on Redis with one atomic round trip."""

    def __init__(
        self, client: _RedisClient | None = None, *, url: str = "redis://localhost:6379/0"
    ) -> None:
        """
        :param client: An existing ``redis.Redis`` (or ``redis.cluster.RedisCluster``)
            instance; default: one created from ``url``.
        :param url: Connection URL used when ``client`` is not given.
        """
        if client is None:
            redis = importlib.import_module("redis")
            from_url = cast("Callable[[str], _RedisClient]", redis.Redis.from_url)
            client = from_url(url)
        self._client = client

    def hit(self, key: str, window_seconds: int) -> tuple[int, float]:
        pipe = self._client.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _created = pipe.execute()
        return count, window_seconds - (time.time() % window_seconds)
