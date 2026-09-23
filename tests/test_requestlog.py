import json
import logging
import sys

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from ninja.testing import TestClient

from ninja_devx import Controller, ControllerOptions, get
from ninja_devx.contrib.apikeys.auth import APIKeyAuth, create_api_key
from ninja_devx.http.middleware import RequestIDMiddleware
from ninja_devx.http.requestlog import (
    JSONFormatter,
    RequestLogMiddleware,
    RequestLogPlugin,
    register_api_key_reader,
)


class Logged(Controller):
    options = ControllerOptions(middleware=[RequestLogMiddleware()])

    @get("/ok")
    def ok(self, request):
        list(User.objects.all())
        return {"ok": True}

    @get("/boom")
    def boom(self, request):
        raise RuntimeError("kaboom")


def _record(caplog, path="/ok", **request_params):
    with caplog.at_level(logging.INFO, logger="ninja_devx.request"):
        response = TestClient(Logged.as_router()).get(path, **request_params)
    assert len(caplog.records) == 1
    return response, caplog.records[0]


@pytest.mark.django_db
def test_emits_one_record_with_otel_field_names(caplog):
    response, record = _record(caplog)
    fields = vars(record)
    assert response.status_code == 200
    assert fields["http.request.method"] == "GET"
    assert fields["url.path"] == "/ok"
    assert fields["http.response.status_code"] == 200
    assert fields["operation"] == "Logged.ok"
    assert fields["duration_ms"] >= 0
    assert fields["query_count"] >= 1
    assert fields["tenant"] is None
    assert fields["api_key_prefix"] is None
    assert "request_id" in fields


@pytest.mark.django_db
def test_flat_naming_uses_plain_field_names(caplog):
    class FlatLogged(Controller):
        options = ControllerOptions(middleware=[RequestLogMiddleware(naming="flat")])

        @get("/ok")
        def ok(self, request):
            return {}

    with caplog.at_level(logging.INFO, logger="ninja_devx.request"):
        TestClient(FlatLogged.as_router()).get("/ok")
    fields = vars(caplog.records[0])
    assert fields["method"] == "GET"
    assert fields["path"] == "/ok"
    assert fields["status"] == 200
    assert "http.request.method" not in fields


@pytest.mark.django_db
def test_user_id_field_reflects_the_authenticated_user(caplog):
    ada = User.objects.create(username="ada")
    _response, record = _record(caplog, user=ada)
    assert vars(record)["user.id"] == ada.pk


@pytest.mark.django_db
def test_tenant_field_reads_request_tenant(caplog):
    class Org:
        pk = 9

    _response, record = _record(caplog, tenant=Org())
    assert vars(record)["tenant"] == 9


@pytest.mark.django_db
def test_api_key_prefix_field_is_set_when_a_key_authenticates(caplog):
    class KeyedController(Controller):
        options = ControllerOptions(auth=APIKeyAuth(), middleware=[RequestLogMiddleware()])

        @get("/ok")
        def ok(self, request):
            return {}

    owner = User.objects.create(username="keyowner")
    key, raw = create_api_key(owner, "ci")
    with caplog.at_level(logging.INFO, logger="ninja_devx.request"):
        TestClient(KeyedController.as_router()).get("/ok", headers={"X-API-Key": raw})
    assert vars(caplog.records[0])["api_key_prefix"] == key.prefix


def test_api_key_prefix_defaults_to_none_without_a_registered_reader():
    from ninja_devx.http import requestlog

    original = requestlog._api_key_reader
    requestlog._api_key_reader = None
    try:
        assert requestlog._api_key_prefix(RequestFactory().get("/ok")) is None
    finally:
        requestlog._api_key_reader = original


def test_register_api_key_reader_is_used():
    from ninja_devx.http import requestlog

    original = requestlog._api_key_reader
    register_api_key_reader(lambda request: "custom-prefix")
    try:
        assert requestlog._api_key_prefix(RequestFactory().get("/ok")) == "custom-prefix"
    finally:
        requestlog._api_key_reader = original


@pytest.mark.django_db
def test_unhandled_exception_logs_an_error_field(caplog):
    with (
        caplog.at_level(logging.INFO, logger="ninja_devx.request"),
        pytest.raises(RuntimeError, match="kaboom"),
    ):
        TestClient(Logged.as_router()).get("/boom")
    assert len(caplog.records) == 1
    fields = vars(caplog.records[0])
    assert fields["error"] == "RuntimeError"
    assert fields["http.response.status_code"] is None


def test_request_log_plugin_bundles_request_id_middleware():
    plugin = RequestLogPlugin()
    kinds = [type(middleware).__name__ for middleware in plugin.middleware()]
    assert kinds == ["RequestIDMiddleware", "RequestLogMiddleware"]


def test_request_log_plugin_can_drop_the_request_id_middleware():
    plugin = RequestLogPlugin(include_request_id=False)
    kinds = [type(middleware).__name__ for middleware in plugin.middleware()]
    assert kinds == ["RequestLogMiddleware"]


@pytest.mark.django_db
def test_request_log_plugin_installed_on_a_router_emits_request_id(caplog):
    class PluginLogged(Controller):
        options = ControllerOptions(middleware=list(RequestLogPlugin().middleware()))

        @get("/ok")
        def ok(self, request):
            return {}

    with caplog.at_level(logging.INFO, logger="ninja_devx.request"):
        response = TestClient(PluginLogged.as_router()).get("/ok")
    assert response.headers["X-Request-ID"]
    assert vars(caplog.records[0])["request_id"] == response.headers["X-Request-ID"]


def test_json_formatter_renders_extra_fields_and_message():
    record = logging.makeLogRecord(
        {
            "msg": "GET /ok -> 200",
            "levelname": "INFO",
            "name": "ninja_devx.request",
            "request_id": "abc123",
            "http.response.status_code": 200,
        }
    )
    payload = json.loads(JSONFormatter().format(record))
    assert payload["message"] == "GET /ok -> 200"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "abc123"
    assert payload["http.response.status_code"] == 200


def test_json_formatter_includes_exception_info():
    try:
        raise ValueError("bad")
    except ValueError:
        record = logging.LogRecord(
            "ninja_devx.request", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    payload = json.loads(JSONFormatter().format(record))
    assert "ValueError" in payload["exc_info"]


def test_structlog_binding_when_structlog_is_installed():
    structlog = pytest.importorskip("structlog")
    contextvars = structlog.contextvars

    class BoundController(Controller):
        options = ControllerOptions(middleware=[RequestIDMiddleware(), RequestLogMiddleware()])

        @get("/ok")
        def ok(self, request):
            return dict(contextvars.get_contextvars())

    response = TestClient(BoundController.as_router()).get("/ok")
    bound = response.json()
    assert bound["request_id"]
    assert not contextvars.get_contextvars()  # cleared after the response


def test_bind_structlog_false_never_touches_contextvars():
    structlog = pytest.importorskip("structlog")
    contextvars = structlog.contextvars

    class UnboundController(Controller):
        options = ControllerOptions(middleware=[RequestLogMiddleware(bind_structlog=False)])

        @get("/ok")
        def ok(self, request):
            return dict(contextvars.get_contextvars())

    response = TestClient(UnboundController.as_router()).get("/ok")
    assert response.json() == {}
