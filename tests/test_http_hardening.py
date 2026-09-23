import json

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from ninja import Router
from ninja.testing import TestClient

from ninja_devx.http.hardening import (
    EnforceContentTypeMiddleware,
    JsonDepthMiddleware,
    MaxBodySizeMiddleware,
)
from ninja_devx.http.middleware import use_middleware
from ninja_devx.http.security import SecurityHeadersMiddleware

_rf = RequestFactory()


def test_security_headers_are_set_and_not_overwritten():
    router = Router()

    @router.get("/ok")
    def ok(request):
        return {"ok": True}

    @router.get("/custom")
    def custom(request):
        response = HttpResponse(b"{}", content_type="application/json")
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response

    use_middleware(router, SecurityHeadersMiddleware(hsts="max-age=60", csp="default-src 'self'"))
    client = TestClient(router)
    response = client.get("/ok")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert response["X-Frame-Options"] == "DENY"
    assert response["Strict-Transport-Security"] == "max-age=60"
    assert client.get("/custom")["X-Frame-Options"] == "SAMEORIGIN"


def test_hardening_constructors_reject_non_positive_limits():
    with pytest.raises(ValueError, match="max_bytes"):
        MaxBodySizeMiddleware(0)
    with pytest.raises(ValueError, match="max_depth"):
        JsonDepthMiddleware(0)


def test_max_body_size_rejects_large_requests():
    middleware = MaxBodySizeMiddleware(20)
    small = _rf.post("/echo", data=b"{}", content_type="application/json")
    assert middleware.process_request(small) is None
    assert middleware.process_request(_rf.get("/echo")) is None
    large = _rf.post("/echo", data=b'{"a": "' + b"x" * 100 + b'"}', content_type="application/json")
    response = middleware.process_request(large)
    assert response is not None
    assert (response.status_code, json.loads(response.content)["code"]) == (
        413,
        "request_too_large",
    )


def test_content_type_is_enforced_for_write_methods_only():
    middleware = EnforceContentTypeMiddleware({"application/json"})
    ok = _rf.post("/echo", data=b"{}", content_type="application/json")
    assert middleware.process_request(ok) is None
    assert middleware.process_request(_rf.get("/echo")) is None
    assert middleware.process_request(_rf.post("/echo", data="", content_type="")) is None
    form = _rf.post("/echo", data={"a": "x"})
    response = middleware.process_request(form)
    assert response is not None
    assert (response.status_code, json.loads(response.content)["code"]) == (
        415,
        "unsupported_media_type",
    )


def test_json_depth_is_limited():
    middleware = JsonDepthMiddleware(max_depth=2)
    ok = _rf.post("/echo", data=json.dumps({"a": {"b": 1}}), content_type="application/json")
    assert middleware.process_request(ok) is None
    deep = _rf.post(
        "/echo", data=json.dumps({"a": {"b": {"c": 1}}}), content_type="application/json"
    )
    response = middleware.process_request(deep)
    assert response is not None
    assert (response.status_code, json.loads(response.content)["code"]) == (400, "json_too_deep")


def test_json_depth_rejects_bodies_the_parser_cannot_nest():
    body = b"[" * 100_000 + b"]" * 100_000
    request = _rf.post("/echo", data=body, content_type="application/json")
    response = JsonDepthMiddleware(max_depth=8).process_request(request)
    assert response is not None
    assert response.status_code == 400


def test_json_depth_skips_non_json_and_malformed_bodies():
    middleware = JsonDepthMiddleware(max_depth=1)
    assert middleware.process_request(_rf.get("/x")) is None
    assert middleware.process_request(_rf.post("/x", data=b"x", content_type="text/plain")) is None
    assert (
        middleware.process_request(_rf.post("/x", data=b"", content_type="application/json"))
        is None
    )
    broken = _rf.post("/x", data=b"{not json", content_type="application/json")
    assert middleware.process_request(broken) is None
    shallow = _rf.post("/x", data=json.dumps([1, 2]), content_type="application/json")
    assert middleware.process_request(shallow) is None


@override_settings(NINJA_DEVX={"ERROR_FORMAT": "problem+json"})
def test_hardening_rejections_follow_the_error_format():
    request = _rf.post("/echo", data={"a": "x"})
    response = EnforceContentTypeMiddleware({"application/json"}).process_request(request)
    assert response is not None
    assert response["Content-Type"] == "application/problem+json"
    assert json.loads(response.content)["status"] == 415
