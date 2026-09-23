import json

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings
from ninja import NinjaAPI
from ninja.testing import TestClient

from ninja_devx import Controller, get
from ninja_devx.tooling.inspect import as_dict, inspect_target, render, resolve
from tests.testapp.api import ArticleController

TARGET = "tests.testapp.api.ArticleController"
CHECK = {"NINJA_DEVX": {"CHECK_APIS": ["tests.inspect_api.api"]}}


class Ping(Controller):
    @get("/")
    def ping(self, request):
        return {"ok": True}


def test_inspect_reports_scoping_permissions_and_operations(db):
    router = ArticleController.as_router()  # keep alive: built routers are weakly held
    assert router is not None
    results = inspect_target(TARGET)
    assert results  # other tests may have mounted the same controller several times
    assert {result.controller for result in results} == {"ArticleController"}
    info = results[0]
    assert info.controller == "ArticleController"
    assert info.model == "Article"
    assert info.permissions == ("IsAuthenticated", "IsOwner")
    assert info.pagination == "PageNumberPagination"
    names = {operation.name for operation in info.operations}
    assert {"list", "create", "summary"} <= names
    summary = next(op for op in info.operations if op.name == "summary")
    assert summary.methods == ("GET",)
    assert summary.path == "/{pk}/summary"


def test_inspect_tree_and_json_are_available(db):
    router = ArticleController.as_router()
    assert router is not None
    info = inspect_target(TARGET)[0]
    tree = render(info)
    assert tree.startswith("ArticleController")
    assert "operations" in tree
    assert "summary" in tree
    payload = as_dict([info])
    assert payload[0]["model"] == "Article"


def test_devx_inspect_command_emits_json(db, capsys):
    router = ArticleController.as_router()
    assert router is not None
    call_command("devx_inspect", TARGET, "--json")
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload[0]["controller"] == "ArticleController"
    assert payload[0]["pagination"] == "PageNumberPagination"


@override_settings(**CHECK)
def test_inspect_walks_check_apis_and_nested_routers():
    results = inspect_target(None)
    names = {result.controller for result in results}
    assert {"Widgets", "ArticleController", "Comments"} <= names
    widgets = next(result for result in results if result.controller == "Widgets")
    assert widgets.transaction == "durable (default)"
    operations = {operation.name: operation for operation in widgets.operations}
    assert "remove" not in operations
    assert operations["create"].idempotent is True
    assert operations["index"].permissions == ("IsAuthenticated",)
    assert operations["index"].atomic == "durable"
    assert operations["slow"].asynchronous is True
    tree = render(widgets)
    assert "idempotent" in tree
    assert "async" in tree
    assert "errors:" in tree


@override_settings(**CHECK)
def test_inspect_resolves_a_controller_path_and_a_prefix():
    assert resolve("tests.inspect_api.Widgets")
    assert resolve("/widgets")
    assert inspect_target("tests.inspect_api.Widgets")[0].model is None


@override_settings(**CHECK)
def test_inspect_reports_relations():
    results = inspect_target("tests.inspect_api.Comments")
    assert results[0].select == ("article",)
    assert "select_related" in render(results[0])


def test_inspect_by_controller_path_needs_no_check_apis():
    api = NinjaAPI()
    api.add_router("/ping", Ping.as_router())
    assert inspect_target("tests.test_inspect.Ping")
    assert TestClient(api).get("/ping/").status_code == 200


def test_prefix_targets_need_check_apis():
    with pytest.raises(LookupError, match="CHECK_APIS"):
        resolve("/ping")
    with pytest.raises(CommandError, match="CHECK_APIS"):
        call_command("devx_inspect", "/ping")


@override_settings(**CHECK)
def test_devx_inspect_reports_unknown_targets():
    with pytest.raises(CommandError, match="No mounted controller matches"):
        call_command("devx_inspect", "/nowhere")
    with pytest.raises(CommandError, match=r"cannot import|No module"):
        call_command("devx_inspect", "tests.nowhere.Missing")
