import json

import pytest
from django.core.management import CommandError, call_command
from django.db import models
from django.test import override_settings
from ninja import Schema

from ninja_devx.crud import ReadOnlyModelController, SoftDeleteMixin
from ninja_devx.tooling.doctor import Finding, run_doctor

CHECK = {"NINJA_DEVX": {"CHECK_APIS": ["tests.inspect_api.api"]}}


class Widget(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True)
    sku = models.CharField(max_length=20, unique=True)

    class Meta:
        app_label = "testapp"


class WidgetOut(Schema):
    id: int
    sku: str


class WidgetController(
    SoftDeleteMixin[Widget, WidgetOut], ReadOnlyModelController[Widget, WidgetOut]
):
    pass


def _for(findings: list[Finding], controller: str) -> list[Finding]:
    return [finding for finding in findings if finding.controller == controller]


@override_settings(**CHECK)
def test_unindexed_ordering_and_default_ordering_fields_are_flagged(db):
    findings = _for(run_doctor("tests.testapp.api.ArticleController"), "ArticleController")
    messages = [finding.message for finding in findings]
    assert any("Article.title" in message for message in messages)
    assert any("Article.created" in message for message in messages)


@override_settings(**CHECK)
def test_owner_field_with_isowner_and_pagination_is_not_flagged(db):
    findings = _for(run_doctor("tests.testapp.api.ArticleController"), "ArticleController")
    assert not any("owner_field" in finding.message for finding in findings)
    assert not any("pagination_class" in finding.message for finding in findings)


@override_settings(**CHECK)
def test_resolver_backed_by_a_real_model_field_is_not_flagged(db):
    # ArticleOut.resolve_tags overrides a real m2m field: the planner already prefetches it.
    findings = _for(run_doctor("tests.testapp.api.ArticleController"), "ArticleController")
    assert not any("resolve_tags" in finding.message for finding in findings)


@override_settings(**CHECK)
def test_controller_without_permissions_is_flagged(db):
    findings = _for(run_doctor("tests.inspect_api.Comments"), "Comments")
    assert any("no permission" in finding.message for finding in findings)


@override_settings(**CHECK)
def test_list_endpoint_without_pagination_is_flagged(db):
    findings = _for(run_doctor("tests.inspect_api.Comments"), "Comments")
    assert any("pagination_class" in finding.message for finding in findings)


@override_settings(**CHECK)
def test_owner_field_without_isowner_is_flagged(db):
    findings = _for(run_doctor("tests.inspect_api.UnsafeOwnerNotes"), "UnsafeOwnerNotes")
    assert any("IsOwner" in finding.message and finding.severity == "warn" for finding in findings)


@override_settings(**CHECK)
def test_tenant_field_without_resolver_is_flagged_as_info(db):
    findings = _for(run_doctor("tests.inspect_api.TenantlessTasks"), "TenantlessTasks")
    matches = [finding for finding in findings if "tenant_resolver" in finding.message]
    assert matches
    assert matches[0].severity == "info"


def test_tenant_field_is_not_flagged_when_a_resolver_is_configured(db):
    settings = {"NINJA_DEVX": {**CHECK["NINJA_DEVX"], "TENANT_RESOLVER": lambda r: None}}
    with override_settings(**settings):
        findings = _for(run_doctor("tests.inspect_api.TenantlessTasks"), "TenantlessTasks")
    assert not any("tenant_resolver" in finding.message for finding in findings)


@override_settings(**CHECK)
def test_unhinted_resolver_field_is_flagged(db):
    findings = _for(run_doctor("tests.inspect_api.ArticleSummaries"), "ArticleSummaries")
    assert any("comment_count" in finding.message for finding in findings)


def test_soft_delete_unique_field_is_flagged(db):
    router = WidgetController.as_router()  # keep alive: built routers are weakly held
    assert router is not None
    findings = _for(run_doctor("tests.test_doctor.WidgetController"), "WidgetController")
    assert any("soft_delete_unique" in finding.hint for finding in findings)
    assert any("sku" in finding.message for finding in findings)


def test_finding_str_includes_severity_controller_message_and_hint():
    finding = Finding("warn", "SomeController", "the message", "the hint")
    text = str(finding)
    assert "warn" in text
    assert "SomeController" in text
    assert "the message" in text
    assert "the hint" in text


def test_run_doctor_raises_on_an_unknown_target(db):
    with pytest.raises(LookupError, match="CHECK_APIS"):
        run_doctor("/nowhere")


@override_settings(**CHECK)
def test_devx_doctor_command_emits_json(db, capsys):
    call_command("devx_doctor", "tests.testapp.api.ArticleController", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert any(item["controller"] == "ArticleController" for item in payload)


@override_settings(**CHECK)
def test_devx_doctor_command_renders_a_table_by_default(db, capsys):
    call_command("devx_doctor", "tests.inspect_api.Comments")
    out = capsys.readouterr().out
    assert "Comments" in out
    assert "->" in out


@override_settings(**CHECK)
def test_devx_doctor_command_fails_on_warn_with_fail_on(db):
    with pytest.raises(CommandError, match="finding"):
        call_command("devx_doctor", "tests.inspect_api.Comments", "--fail-on", "warn")


@override_settings(**CHECK)
def test_devx_doctor_command_succeeds_without_fail_on(db, capsys):
    call_command("devx_doctor", "tests.inspect_api.Comments")
    assert capsys.readouterr().out
