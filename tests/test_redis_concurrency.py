"""Atomic counter contract against a real, externally supplied Redis instance."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.cache import caches
from django.test import RequestFactory

from ninja_devx.http.throttling import RateThrottle


@pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="requires real Redis")
def test_fixed_window_counter_is_atomic_across_threads(settings):
    settings.CACHES = {
        **settings.CACHES,
        "backend": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ["TEST_REDIS_URL"],
            "KEY_PREFIX": "ninja-devx-review",
            "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
        },
    }

    class Counter(RateThrottle):
        scope = "review-" + uuid.uuid4().hex

        def identify(self, request):
            return "one-client"

    throttle = Counter("20/day", cache="backend")

    def attempt(_):
        try:
            return throttle.allow_request(RequestFactory().get("/"))
        finally:
            caches.close_all()

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(attempt, range(100)))
    assert sum(outcomes) == 20
