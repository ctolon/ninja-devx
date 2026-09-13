"""Wire-level request/response contracts of generated sync and async clients."""

import httpx
import pytest

from ninja_devx.codegen import generate_python, generate_typescript
from tests.test_codegen import load_client


def test_success_status_selects_model_even_when_shapes_overlap(tmp_path):
    document = {
        "info": {"title": "overlapping responses"},
        "components": {
            "schemas": {
                "Existing": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
                "Created": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
            }
        },
        "paths": {
            "/run": {
                "post": {
                    "operationId": "run",
                    "responses": {
                        status: {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": f"#/components/schemas/{name}"}
                                }
                            }
                        }
                        for status, name in [("200", "Existing"), ("201", "Created")]
                    },
                }
            }
        },
    }
    module = load_client(tmp_path, generate_python(document))
    for status, expected in [(200, module.Existing), (201, module.Created)]:
        transport = httpx.MockTransport(
            lambda request, status=status: httpx.Response(status, json={"id": 1})
        )
        with httpx.Client(transport=transport, base_url="https://test") as http:
            assert type(module.ApiClient(client=http).run()) is expected


def contract_document():
    return {
        "openapi": "3.1.0",
        "info": {"title": "HTTP contract", "version": "1"},
        "paths": {
            "/run": {
                "parameters": [{"$ref": "#/components/parameters/Key"}],
                "post": {
                    "operationId": "run",
                    "parameters": [
                        {
                            "name": "mode",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {"name": "If-Match", "in": "header", "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "required": False,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "Existing or export",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Existing"}
                                },
                                "text/csv": {"schema": {"type": "string"}},
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                },
                            },
                        },
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Created"}
                                }
                            },
                        },
                        "204": {"description": "Empty"},
                    },
                },
            }
        },
        "components": {
            "parameters": {
                "Key": {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                }
            },
            "schemas": {
                "Existing": {
                    "type": "object",
                    "required": ["kind", "id"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["existing"]},
                        "id": {"type": "integer"},
                    },
                },
                "Created": {
                    "type": "object",
                    "required": ["kind", "message"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["created"]},
                        "message": {"type": "string"},
                    },
                },
            },
        },
    }


def respond(request):
    assert request.headers["Idempotency-Key"] == "same"
    assert request.headers["If-Match"] == '"version-1"'
    assert request.headers["X-Custom"] == "extra"
    mode = request.url.params["mode"]
    headers = {"ETag": '"version-2"', "X-Result": mode}
    if mode == "created":
        return httpx.Response(201, json={"kind": "created", "message": "ok"}, headers=headers)
    if mode == "empty":
        return httpx.Response(204, headers=headers)
    if mode == "csv":
        return httpx.Response(
            200, text="id,name\n1,Ada\n", headers={**headers, "Content-Type": "text/csv"}
        )
    if mode == "binary":
        return httpx.Response(
            200,
            content=b"\x00\xff\x01",
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
    if mode == "html-error":
        return httpx.Response(
            500, text="<html>failed</html>", headers={**headers, "Content-Type": "text/html"}
        )
    if mode == "bad-json-error":
        return httpx.Response(
            502, text="not JSON", headers={**headers, "Content-Type": "application/json"}
        )
    return httpx.Response(200, json={"kind": "existing", "id": 7}, headers=headers)


def arguments(mode, observed):
    return {
        "mode": mode,
        "Idempotency_Key": "same",
        "If_Match": '"version-1"',
        "request_headers": {"X-Custom": "extra"},
        "on_response": observed.append,
    }


def assert_result(value, mode, style):
    if mode == "empty":
        assert value is None
    elif mode == "csv":
        assert value == "id,name\n1,Ada\n"
    elif mode == "binary":
        assert value == b"\x00\xff\x01"
    else:
        if style == "pydantic":
            value = value.model_dump()
        assert value["kind"] == mode


@pytest.mark.parametrize("style", ["pydantic", "typeddict"])
def test_sync_generated_headers_success_variants_and_non_json_errors(tmp_path, style):
    module = load_client(tmp_path, generate_python(contract_document(), style=style))
    observed = []
    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="https://example.test"
    ) as http:
        client = module.ApiClient(client=http)
        for mode in ("existing", "created", "empty", "csv", "binary"):
            assert_result(client.run(**arguments(mode, observed)), mode, style)
            assert observed[-1].headers["ETag"] == '"version-2"'
        for mode, code in [("html-error", 500), ("bad-json-error", 502)]:
            with pytest.raises(module.ApiError) as failure:
                client.run(**arguments(mode, observed))
            assert failure.value.status_code == code
            assert isinstance(failure.value.body, str)
            assert failure.value.headers["x-result"] == mode


@pytest.mark.parametrize("style", ["pydantic", "typeddict"])
async def test_async_generated_contract_matches_sync(tmp_path, style):
    module = load_client(tmp_path, generate_python(contract_document(), style=style))
    observed = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.test"
    ) as http:
        client = module.AsyncApiClient(client=http)
        for mode in ("existing", "created", "empty", "csv", "binary"):
            assert_result(await client.run(**arguments(mode, observed)), mode, style)
        with pytest.raises(module.ApiError) as failure:
            await client.run(**arguments("html-error", observed))
        assert failure.value.status_code == 500


@pytest.mark.parametrize("generator", [generate_python, generate_typescript])
@pytest.mark.parametrize("feature", ["multipart", "cookie", "stream", "style"])
def test_unsupported_contracts_fail_instead_of_disappearing(generator, feature):
    spec = contract_document()
    operation = spec["paths"]["/run"]["post"]
    if feature == "multipart":
        operation["requestBody"] = {
            "required": True,
            "content": {"multipart/form-data": {"schema": {"type": "object"}}},
        }
    elif feature == "cookie":
        operation["parameters"].append(
            {"name": "session", "in": "cookie", "required": True, "schema": {"type": "string"}}
        )
    elif feature == "stream":
        operation["responses"]["200"]["content"] = {
            "text/event-stream": {"schema": {"type": "string"}}
        }
    else:
        operation["parameters"][0]["style"] = "deepObject"
    with pytest.raises(ValueError, match="unsupported"):
        generator(spec)


def test_typescript_http_contract_compiles_and_sends_headers(tmp_path):
    import shutil
    import subprocess

    npx, node = shutil.which("npx"), shutil.which("node")
    if not npx or not node:
        pytest.skip("requires Node and TypeScript")
    source = tmp_path / "client.ts"
    source.write_text(generate_typescript(contract_document()))
    compiled = subprocess.run(
        [
            npx,
            "-y",
            "-p",
            "typescript@5.9.3",
            "tsc",
            "--strict",
            "--target",
            "es2022",
            "--module",
            "commonjs",
            "--lib",
            "es2022,dom",
            "--skipLibCheck",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    script = tmp_path / "check.cjs"
    script.write_text("""const assert = require('node:assert/strict');
const {createClient, ApiError} = require('./client.js');
const observed = [];
const client = createClient({baseUrl: 'https://example.test', fetch: async (url, init) => {
  const headers = new Headers(init.headers);
  assert.equal(headers.get('Idempotency-Key'), 'same');
  assert.equal(headers.get('If-Match'), '"version-1"');
  assert.equal(headers.get('X-Custom'), 'extra');
  const mode = new URL(url).searchParams.get('mode');
  const out = {'ETag': '"version-2"'};
  const reply = (status, body, type) => new Response(body, {
    status, headers: {...out, 'Content-Type': type},
  });
  if (mode === 'empty') return new Response(null, {status: 204, headers: out});
  if (mode === 'csv') return reply(200, 'id,name\\n1,Ada\\n', 'text/csv');
  if (mode === 'binary') {
    return reply(200, new Uint8Array([0, 255, 1]), 'application/octet-stream');
  }
  if (mode === 'html-error') return reply(500, '<html>failed</html>', 'text/html');
  if (mode === 'bad-json-error') return reply(502, 'not JSON', 'application/json');
  const data = mode === 'created' ? {kind: 'created', message: 'ok'} : {kind: 'existing', id: 7};
  return reply(mode === 'created' ? 201 : 200, JSON.stringify(data), 'application/json');
}});
const run = mode => client.run(undefined, {mode}, {
  'Idempotency-Key': 'same', 'If-Match': '"version-1"',
}, {
  headers: {'X-Custom': 'extra'},
  onResponse: response => observed.push([response.status, response.headers.get('ETag')]),
});
(async () => {
  assert.deepEqual(await run('existing'), {kind: 'existing', id: 7});
  assert.deepEqual(await run('created'), {kind: 'created', message: 'ok'});
  assert.equal(await run('empty'), undefined);
  assert.equal(await run('csv'), 'id,name\\n1,Ada\\n');
  assert.deepEqual(await run('binary'), new Uint8Array([0, 255, 1]));
  for (const mode of ['html-error', 'bad-json-error']) {
    await assert.rejects(() => run(mode), error => (
      error instanceof ApiError && typeof error.body === 'string' &&
      error.headers.get('ETag') === '"version-2"'
    ));
  }
  assert.equal(observed.length, 7);
  assert.equal(observed[1][0], 201);
  assert.ok(observed.every(item => item[1] === '"version-2"'));
})().catch(error => {console.error(error); process.exitCode = 1;});
""")
    ran = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=10, check=False
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_generation_failure_does_not_replace_an_existing_file(tmp_path, monkeypatch):
    from django.core.management import CommandError, call_command

    from tests.urls import api

    spec = contract_document()
    spec["paths"]["/run"]["post"]["requestBody"] = {
        "required": True,
        "content": {"multipart/form-data": {"schema": {"type": "object"}}},
    }
    monkeypatch.setattr(api, "get_openapi_schema", lambda **kwargs: spec)
    output = tmp_path / "client.py"
    output.write_text("keep me")
    with pytest.raises(CommandError, match="unsupported"):
        call_command(
            "devx_openapi", "tests.urls.api", "--format", "python", "--output", str(output)
        )
    assert output.read_text() == "keep me"
