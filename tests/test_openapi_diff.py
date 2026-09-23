import json

import pytest
from django.core.management import CommandError, call_command

from ninja_devx.tooling.openapi_diff import ADDITIVE, BREAKING, diff, has_breaking
from tests.urls import api


def document(schemas=None, **paths):
    return {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": paths,
        "components": {"schemas": schemas or {}},
    }


def details(changes):
    return " ".join(change.detail for change in changes)


def json_op(schema):
    return {
        "responses": {
            "200": {"description": "ok", "content": {"application/json": {"schema": schema}}}
        }
    }


def test_removed_paths_and_operations_are_breaking():
    baseline = document(**{"/a": {"get": json_op({"type": "object"})}})
    current = document()
    changes = diff(baseline, current)
    assert has_breaking(changes)
    assert any(change.severity == BREAKING and "removed" in change.detail for change in changes)


def test_added_paths_and_optional_fields_are_additive():
    baseline = document(
        **{"/a": {"get": json_op({"type": "object", "properties": {"x": {"type": "string"}}})}}
    )
    current = document(
        **{
            "/a": {
                "get": json_op(
                    {
                        "type": "object",
                        "properties": {"x": {"type": "string"}, "y": {"type": "integer"}},
                    }
                )
            },
            "/b": {"get": json_op({"type": "object"})},
        }
    )
    changes = diff(baseline, current)
    assert not has_breaking(changes)
    assert any("field added" in change.detail for change in changes)


def test_added_required_request_field_and_type_change_are_breaking():
    baseline = document(
        **{
            "/a": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    **json_op({"type": "object"}),
                }
            }
        }
    )
    current = document(
        **{
            "/a": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    **json_op({"type": "object", "properties": {"ok": {"type": "boolean"}}}),
                }
            }
        }
    )
    changes = diff(baseline, current)
    assert has_breaking(changes)


def test_added_required_parameter_is_breaking():
    baseline = document(**{"/a": {"get": {"parameters": [], **json_op({"type": "object"})}}})
    current = document(
        **{
            "/a": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "q", "required": True, "schema": {"type": "string"}}
                    ],
                    **json_op({"type": "object"}),
                }
            }
        }
    )
    assert has_breaking(diff(baseline, current))


def test_enum_type_and_required_changes():
    baseline = document(
        **{
            "/a": {
                "get": json_op(
                    {"type": "object", "properties": {"x": {"type": "string", "enum": ["a"]}}}
                )
            }
        }
    )
    current = document(
        **{
            "/a": {
                "get": json_op(
                    {"type": "object", "properties": {"x": {"type": "integer", "enum": ["b"]}}}
                )
            }
        }
    )
    changes = diff(baseline, current)
    assert has_breaking(changes)
    assert "type changed" in details(changes)
    assert "enum values removed" in details(changes)
    assert "enum values added" in details(changes)


def test_response_and_request_body_removal_are_breaking():
    baseline = document(
        **{
            "/a": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {
                        "200": json_op({"type": "object"})["responses"]["200"],
                        "400": {
                            "description": "bad",
                            "content": {"application/json": {"schema": {"type": "string"}}},
                        },
                    },
                }
            }
        }
    )
    current = document(**{"/a": {"post": json_op({"type": "object"})}})
    changes = diff(baseline, current)
    assert "response 400 removed" in details(changes)
    assert "request body removed" in details(changes)


def query_parameter(required):
    return {"in": "query", "name": "q", "required": required, "schema": {"type": "string"}}


def test_parameter_requirement_changes():
    optional = document(
        **{"/a": {"get": {"parameters": [query_parameter(False)], **json_op({"type": "object"})}}}
    )
    required = document(
        **{"/a": {"get": {"parameters": [query_parameter(True)], **json_op({"type": "object"})}}}
    )
    assert has_breaking(diff(optional, required))
    relaxed = diff(required, optional)
    assert not has_breaking(relaxed)
    assert "became optional" in details(relaxed)


def test_parameter_removed():
    baseline = document(
        **{"/a": {"get": {"parameters": [query_parameter(False)], **json_op({"type": "object"})}}}
    )
    current = document(**{"/a": {"get": json_op({"type": "object"})}})
    assert "parameter 'q' removed" in details(diff(baseline, current))


def test_unknown_refs_do_not_crash():
    schema = {"type": "object", "properties": {"x": {"$ref": "#/nope"}}}
    doc = document(**{"/a": {"get": json_op(schema)}})
    assert diff(doc, doc) == []


def test_recursive_schemas_are_compared_once():
    def doc(tag_type):
        node = {
            "type": "object",
            "properties": {
                "parent": {"$ref": "#/components/schemas/Node"},
                "tags": {"type": "array", "items": {"type": tag_type}},
            },
        }
        return document(
            {"Node": node}, **{"/n": {"get": json_op({"$ref": "#/components/schemas/Node"})}}
        )

    assert diff(doc("string"), doc("string")) == []
    changes = diff(doc("string"), doc("integer"))
    assert [change.location for change in changes] == ["GET /n 200.tags[]"]
    assert has_breaking(changes)


def test_shared_schemas_are_compared_at_every_location():
    def doc(value_type):
        leaf = {"type": "object", "properties": {"v": {"type": value_type}}}
        node = {
            "type": "object",
            "properties": {
                "a": {"$ref": "#/components/schemas/Leaf"},
                "b": {"$ref": "#/components/schemas/Leaf"},
            },
        }
        return document(
            {"Node": node, "Leaf": leaf},
            **{"/n": {"get": json_op({"$ref": "#/components/schemas/Node"})}},
        )

    changes = diff(doc("string"), doc("integer"))
    assert [change.location for change in changes] == ["GET /n 200.a.v", "GET /n 200.b.v"]


def test_nested_field_removal_and_required_toggles():
    item = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    baseline = document(
        **{"/a": {"get": json_op({"type": "object", "properties": {"item": item}})}}
    )
    current = document(
        **{"/a": {"get": json_op({"type": "object", "properties": {"item": {"type": "object"}}})}}
    )
    assert "field removed" in details(diff(baseline, current))

    required = document(**{"/a": {"get": json_op(item)}})
    optional = document(**{"/a": {"get": json_op({**item, "required": []})}})
    assert "became optional" in details(diff(required, optional))
    assert "became required" in details(diff(optional, required))


def test_non_json_bodies_and_operation_changes():
    text_body = {
        "requestBody": {"content": {"text/plain": {"schema": {"type": "string"}}}},
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "text/plain": {"schema": {"type": "string"}},
                    "application/json": {"schema": {"type": "object"}},
                },
            }
        },
    }
    baseline = document(**{"/a": {"get": json_op({"type": "object"}), "post": text_body}})
    assert diff(baseline, baseline) == []
    current = document(**{"/a": {"get": json_op({"type": "object"})}})
    assert "operation removed" in details(diff(baseline, current))
    added = diff(current, baseline)
    assert "operation added" in details(added)
    assert {change.severity for change in added} == {ADDITIVE}


def test_command_against_passes_then_fails(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(api.get_openapi_schema()))
    call_command("devx_openapi", "tests.urls.api", "--against", str(baseline))

    stale = api.get_openapi_schema()
    extra = next(iter(stale["paths"]))
    stale["paths"]["/removed"] = stale["paths"][extra]
    baseline.write_text(json.dumps(stale))
    with pytest.raises(CommandError, match="Breaking"):
        call_command("devx_openapi", "tests.urls.api", "--against", str(baseline))


def test_command_against_writes_the_new_baseline(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(api.get_openapi_schema()))
    output = tmp_path / "openapi.json"
    call_command(
        "devx_openapi", "tests.urls.api", "--against", str(baseline), "--output", str(output)
    )
    assert set(json.loads(output.read_text())["paths"]) == set(api.get_openapi_schema()["paths"])
