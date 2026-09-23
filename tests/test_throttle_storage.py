import os
from dataclasses import dataclass, field

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory

from ninja_devx.http.throttling import (
    CacheThrottleStorage,
    RateThrottle,
    ThrottleStorage,
    UserRateThrottle,
)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    global_storage.hits.clear()


@dataclass
class _RecordingStorage:
    hits: list[tuple[str, int]] = field(default_factory=list)
    retry_after: float = 42.0

    def hit(self, key: str, window_seconds: int) -> tuple[int, float]:
        self.hits.append((key, window_seconds))
        return len(self.hits), self.retry_after


global_storage = _RecordingStorage()


def test_cache_throttle_storage_counts_and_reports_retry_after():
    storage = CacheThrottleStorage()
    count, retry_after = storage.hit("k1", 60)
    assert count == 1
    assert 0 < retry_after <= 60
    count, retry_after = storage.hit("k1", 60)
    assert count == 2
    count, retry_after = storage.hit("k2", 60)
    assert count == 1  # a different key starts its own window


def test_cache_throttle_storage_is_a_throttle_storage():
    assert isinstance(CacheThrottleStorage(), ThrottleStorage)
    assert isinstance(global_storage, ThrottleStorage)
    assert not isinstance(object(), ThrottleStorage)


@pytest.mark.django_db
def test_explicit_storage_kwarg_overrides_cache():
    storage = _RecordingStorage()
    throttle = UserRateThrottle("2/min", storage=storage)
    ada = User.objects.create(username="ada")
    request = RequestFactory().get("/")
    request.auth = ada

    assert throttle.allow_request(request) is True
    assert throttle.allow_request(request) is True
    assert throttle.allow_request(request) is False  # storage reports counts 1, 2, 3
    assert throttle.wait() == storage.retry_after
    assert len(storage.hits) == 3


@pytest.mark.django_db
def test_throttle_storage_setting_is_used_without_an_explicit_storage(settings):
    settings.NINJA_DEVX = {"THROTTLE_STORAGE": "tests.test_throttle_storage.global_storage"}
    throttle = UserRateThrottle("100/min")
    ada = User.objects.create(username="ada")
    request = RequestFactory().get("/")
    request.auth = ada

    assert throttle.allow_request(request) is True
    assert global_storage.hits


@pytest.mark.django_db
def test_throttle_storage_setting_rejects_a_non_storage_object(settings):
    settings.NINJA_DEVX = {"THROTTLE_STORAGE": "tests.test_throttle_storage.User"}
    throttle = UserRateThrottle("100/min")
    ada = User.objects.create(username="ada")
    request = RequestFactory().get("/")
    request.auth = ada

    with pytest.raises(ImproperlyConfigured, match="THROTTLE_STORAGE"):
        throttle.allow_request(request)


def test_explicit_storage_wins_over_the_setting(settings):
    settings.NINJA_DEVX = {"THROTTLE_STORAGE": "tests.test_throttle_storage.global_storage"}
    own_storage = _RecordingStorage()

    class _Anon(RateThrottle):
        def identify(self, request):
            return "one"

    anon_throttle = _Anon("100/min", storage=own_storage)
    assert anon_throttle.allow_request(RequestFactory().get("/")) is True
    assert own_storage.hits
    assert not global_storage.hits


class _FakePipeline:
    def __init__(self, client: "_FakeRedisClient") -> None:
        self._client = client
        self._ops: list[tuple[object, ...]] = []

    def incr(self, name: str) -> "_FakePipeline":
        self._ops.append(("incr", name))
        return self

    def expire(self, name: str, time: int, *, nx: bool = False) -> "_FakePipeline":
        self._ops.append(("expire", name, time, nx))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for op in self._ops:
            self._client.calls.append(op)
            if op[0] == "incr":
                _, name = op
                self._client.counts[name] = self._client.counts.get(name, 0) + 1
                results.append(self._client.counts[name])
            else:
                _, name, _ttl, nx = op
                if not nx or name not in self._client.ttl_set:
                    self._client.ttl_set.add(name)
                results.append(True)
        self._ops = []
        return results


class _FakeRedisClient:
    """A tiny stand-in for the two redis-py commands ``RedisThrottleStorage`` pipelines."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttl_set: set[str] = set()
        self.calls: list[tuple[object, ...]] = []

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)


def test_redis_throttle_storage_pipelines_incr_and_conditional_expire():
    from ninja_devx.contrib.redis_throttle import RedisThrottleStorage

    client = _FakeRedisClient()
    storage = RedisThrottleStorage(client=client)

    count, retry_after = storage.hit("k", 60)
    assert count == 1
    assert 0 < retry_after <= 60
    count, _retry_after = storage.hit("k", 60)
    assert count == 2
    count, _retry_after = storage.hit("other", 60)
    assert count == 1

    assert client.calls == [
        ("incr", "k"),
        ("expire", "k", 60, True),
        ("incr", "k"),
        ("expire", "k", 60, True),
        ("incr", "other"),
        ("expire", "other", 60, True),
    ]
    assert isinstance(storage, ThrottleStorage)


@pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="requires real Redis")
def test_redis_throttle_storage_is_atomic_across_threads():
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    from ninja_devx.contrib.redis_throttle import RedisThrottleStorage

    storage = RedisThrottleStorage(url=os.environ["TEST_REDIS_URL"])
    key = f"ninja-devx-test:{uuid.uuid4().hex}"

    def hit(_: int) -> int:
        count, _retry_after = storage.hit(key, 60)
        return count

    with ThreadPoolExecutor(max_workers=16) as pool:
        counts = list(pool.map(hit, range(100)))

    assert sorted(counts) == list(range(1, 101))
