import logging
from typing import Annotated

import pytest
from django.contrib.auth.models import User
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient
from pydantic import Field

from ninja_devx import ControllerOptions
from ninja_devx.contrib.audit.privacy import AuditPrivacy
from ninja_devx.crud import ReadOnlyModelController
from ninja_devx.crud.transfer import ExportMixin
from ninja_devx.http.errors import mask_validation_input
from ninja_devx.http.requestlog import RequestLogMiddleware
from ninja_devx.routing.controller import Controller
from ninja_devx.routing.operations import post
from ninja_devx.serialization.privacy import Sensitive, mask, redact_payload, sensitive_fields
from tests.testapp.models import Article

pytestmark = pytest.mark.django_db


class SecretIn(Schema):
    title: str
    body: Annotated[str, Sensitive()]
    aliased: Annotated[str, Sensitive()] = Field(default="", alias="aliasedName")


def test_sensitive_fields_returns_annotated_names_and_aliases():
    assert sensitive_fields(SecretIn) == {"body", "aliased", "aliasedName"}


def test_sensitive_fields_is_empty_without_any_marker():
    class Plain(Schema):
        title: str

    assert sensitive_fields(Plain) == frozenset()


def test_mask_replaces_scalars_and_none_and_lists():
    assert mask("hunter2") == "***"
    assert mask(None) is None
    assert mask(["a", None, "b"]) == ["***", None, "***"]


def test_redact_payload_masks_only_sensitive_keys():
    data = {"title": "hello", "body": "hunter2", "aliasedName": "shh"}
    assert redact_payload(SecretIn, data) == {
        "title": "hello",
        "body": "***",
        "aliasedName": "***",
    }


def test_redact_payload_is_a_no_op_without_sensitive_fields():
    class Plain(Schema):
        title: str

    data = {"title": "hello"}
    result = redact_payload(Plain, data)
    assert result == data
    assert result is not data


class ArticleOut(Schema):
    id: int
    title: str
    body: Annotated[str, Sensitive()]


class Articles(ExportMixin[Article, ArticleOut], ReadOnlyModelController[Article, ArticleOut]):
    pass


class SensitiveArticles(Articles):
    export_sensitive = True


@pytest.fixture
def article():
    return Article.objects.create(
        title="Q3 plan", slug="q3-plan", body="the secret roadmap", author=User.objects.create()
    )


def test_export_masks_sensitive_fields_by_default_csv(article):
    response = TestClient(Articles.as_router()).get("/export?format=csv")
    body = response.content.decode()
    assert "the secret roadmap" not in body
    assert "***" in body


def test_export_masks_sensitive_fields_by_default_jsonl(article):
    response = TestClient(Articles.as_router()).get("/export?format=jsonl")
    line = response.content.decode().strip()
    assert "the secret roadmap" not in line
    assert '"body":"***"' in line


def test_export_sensitive_true_keeps_the_raw_value(article):
    response = TestClient(SensitiveArticles.as_router()).get("/export?format=jsonl")
    line = response.content.decode().strip()
    assert "the secret roadmap" in line


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_export_masks_sensitive_fields(article):
    class AsyncArticles(Articles):
        mode = "async"

    response = await TestAsyncClient(AsyncArticles.as_router()).get("/export?format=jsonl")
    line = response.content.decode().strip()
    assert "the secret roadmap" not in line
    assert '"body":"***"' in line


def test_audit_privacy_treats_the_core_marker_like_its_own_redact_names():
    policy = AuditPrivacy(schema=SecretIn)
    assert policy.changes({"title": "hello", "body": "hunter2"}) == {
        "title": "hello",
        "body": "***",
    }


def test_audit_privacy_without_a_schema_only_uses_its_own_redact_tuple():
    policy = AuditPrivacy()
    assert policy.changes({"title": "hello", "body": "hunter2"}) == {
        "title": "hello",
        "body": "hunter2",
    }


def test_mask_validation_input_masks_only_the_sensitive_fields_input():
    errors = [
        {"type": "string_type", "loc": ["body", "title"], "msg": "bad", "input": "ok"},
        {"type": "string_type", "loc": ["body", "body"], "msg": "bad", "input": "hunter2"},
    ]
    masked = mask_validation_input(errors, SecretIn)
    assert masked[0]["input"] == "ok"
    assert masked[1]["input"] == "***"


def test_mask_validation_input_without_a_schema_leaves_input_untouched():
    errors = [{"type": "string_type", "loc": ["body", "body"], "msg": "bad", "input": "hunter2"}]
    assert mask_validation_input(errors) == errors


def test_mask_validation_input_copies_rather_than_mutates():
    errors = [{"type": "string_type", "loc": ["body", "body"], "msg": "bad", "input": "hunter2"}]
    mask_validation_input(errors, SecretIn)
    assert errors[0]["input"] == "hunter2"


class LoggedSecrets(Controller):
    options = ControllerOptions(middleware=[RequestLogMiddleware()])

    @post("/secrets")
    def create(self, request, payload: SecretIn):
        return {"ok": True}


def test_request_log_never_carries_the_request_body(caplog):
    with caplog.at_level(logging.INFO, logger="ninja_devx.request"):
        TestClient(LoggedSecrets.as_router()).post(
            "/secrets", json={"title": "hi", "body": "hunter2", "aliasedName": ""}
        )
    assert len(caplog.records) == 1
    for value in vars(caplog.records[0]).values():
        assert value != "hunter2"
        if isinstance(value, str):
            assert "hunter2" not in value
