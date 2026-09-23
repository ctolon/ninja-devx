import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from ninja import NinjaAPI
from ninja.testing import TestClient

from ninja_devx.http.cache import ResponseCacheMiddleware, invalidate_cache
from ninja_devx.http.middleware import use_middleware

_rf = RequestFactory()
_CALLS: list[int] = []


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def test_cache_response_hits_and_invalidates():
    _CALLS.clear()
    api = NinjaAPI()
    use_middleware(api, ResponseCacheMiddleware(ttl=60, key_prefix="count"))

    @api.get("/count")
    def count(request):
        _CALLS.append(1)
        return {"n": len(_CALLS)}

    invalidate_cache("count")
    client = TestClient(api)
    first = client.get("/count")
    assert first.json() == {"n": 1}
    assert first["Cache-Control"] == "public, max-age=60"
    cached = client.get("/count")
    assert cached.json() == {"n": 1}
    assert cached["X-Cache"] == "HIT"
    assert cached["Cache-Control"] == "public, max-age=60"
    assert len(_CALLS) == 1

    invalidate_cache("count")
    assert client.get("/count").json() == {"n": 2}


def test_cache_response_varies_on_headers():
    api = NinjaAPI()
    use_middleware(api, ResponseCacheMiddleware(ttl=60, key_prefix="who", vary_on=("X-User",)))

    @api.get("/who")
    def who(request):
        return {"user": request.headers.get("X-User", "")}

    client = TestClient(api)
    assert client.get("/who", headers={"X-User": "ada"}).json() == {"user": "ada"}
    assert client.get("/who", headers={"X-User": "bob"}).json() == {"user": "bob"}
    assert "X-User" in client.get("/who", headers={"X-User": "ada"})["Vary"]


def test_credential_headers_make_the_cache_private():
    middleware = ResponseCacheMiddleware(ttl=30, key_prefix="auth", vary_on=("authorization",))
    response = middleware.process_response(_rf.get("/x"), HttpResponse(b"{}"))
    assert response["Cache-Control"] == "private, max-age=30"
    hit = middleware.process_request(_rf.get("/x"))
    assert hit is not None
    assert hit["Cache-Control"] == "private, max-age=30"
    assert hit["Vary"] == "authorization"


def test_cache_ignores_other_methods_uncacheable_statuses_and_hits():
    middleware = ResponseCacheMiddleware(ttl=5, key_prefix="x")
    assert middleware.process_request(_rf.post("/x", data={})) is None
    failure = HttpResponse(b"boom", status=500)
    assert middleware.process_response(_rf.get("/x"), failure) is failure
    assert "Cache-Control" not in failure
    hit = HttpResponse(b"{}")
    hit["X-Cache"] = "HIT"
    assert middleware.process_response(_rf.get("/x"), hit) is hit


def test_cache_keeps_headers_the_view_set():
    middleware = ResponseCacheMiddleware(ttl=5, key_prefix="v", vary_on=("X-User",))
    response = HttpResponse(b"{}")
    response["Vary"] = "X-Other"
    response["Cache-Control"] = "no-store"
    assert middleware.process_response(_rf.get("/x"), response) is response
    assert response["Vary"] == "X-Other"
    assert response["Cache-Control"] == "no-store"


def test_invalidation_survives_a_lost_version_key():
    middleware = ResponseCacheMiddleware(ttl=60, key_prefix="lost")
    request = _rf.get("/x")
    middleware.process_request(request)
    middleware.process_response(request, HttpResponse(b"old"))
    cache.delete("ninja_devx:cache-version:lost")
    invalidate_cache("lost")
    assert middleware.process_request(_rf.get("/x")) is None
