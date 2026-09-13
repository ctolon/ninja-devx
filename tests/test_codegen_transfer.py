"""Generated clients against the actual import/export controllers over Django HTTP handlers."""

from threading import current_thread, main_thread
from types import ModuleType
from uuid import uuid4

import httpx
import pytest
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import WSGIHandler
from django.core.signals import request_finished
from django.db import connections
from django.test import override_settings
from django.urls import path
from ninja import NinjaAPI, Schema

from ninja_devx.codegen import generate_python, generate_typescript, read_operations
from ninja_devx.codegen.openapi import named_multipart_bodies, ref_name
from ninja_devx.crud import CRUDController
from ninja_devx.crud.transfer import ExportMixin, ImportMixin
from tests.test_codegen import load_client
from tests.testapp.models import Tag

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def close_asgi_test_connections():
    # Django intentionally leaves shared in-memory SQLite open. ASGI request-local
    # worker threads end after each request, so close their handles while preserving
    # the main test connection that owns the in-memory database.
    def close_worker(**kwargs):
        if current_thread() is main_thread():
            return
        for connection in connections.all(initialized_only=True):
            if connection.vendor == "sqlite" and connection.connection is not None:
                connection.connection.close()
                connection.connection = None

    request_finished.connect(close_worker, weak=False)
    try:
        yield
    finally:
        request_finished.disconnect(close_worker)


class TagIn(Schema):
    name: str


class TagOut(TagIn):
    id: int


def transfer_api(mode):
    class Tags(
        ImportMixin[Tag, TagIn], ExportMixin[Tag, TagOut], CRUDController[Tag, TagOut, TagIn]
    ):
        pass

    Tags.mode = mode
    api = NinjaAPI(urls_namespace=f"transfer-client-{uuid4().hex}")
    api.add_router("/tags", Tags.as_router())
    urls = ModuleType("generated_transfer_urls")
    urls.urlpatterns = [path("", api.urls)]
    return api, urls


def generated(tmp_path, api, style):
    document = named_multipart_bodies(api.get_openapi_schema(path_prefix=""))
    operation = next(op for op in read_operations(document) if op.path == "/tags/import")
    module = load_client(tmp_path, generate_python(document, style=style))
    values = {"file": module.UploadFile("tags.csv", b"name\nfrom-client\n", "text/csv")}
    assert operation.body_media_type == "multipart/form-data"
    body = (
        getattr(module, ref_name(operation.body["$ref"]))(**values)
        if style == "pydantic"
        else values
    )
    return module, body


@pytest.mark.parametrize("style", ["pydantic", "typeddict"])
def test_sync_client_imports_and_exports_real_controller(tmp_path, style):
    api, urls = transfer_api("sync")
    module, body = generated(tmp_path, api, style)
    with (
        override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[]),
        httpx.Client(
            transport=httpx.WSGITransport(app=WSGIHandler()), base_url="http://testserver"
        ) as http,
    ):
        client = module.ApiClient(client=http)
        result = client.tags_import_rows(body, request_headers={"Content-Type": "application/json"})
        assert result is not None
        assert Tag.objects.filter(name="from-client").exists()
        exported = client.tags_export(format="csv")
        assert "from-client" in exported


@pytest.mark.parametrize("style", ["pydantic", "typeddict"])
async def test_async_client_imports_and_exports_real_controller(tmp_path, style):
    api, urls = transfer_api("async")
    module, body = generated(tmp_path, api, style)
    with override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[]):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ASGIHandler()), base_url="http://testserver"
        ) as http:
            client = module.AsyncApiClient(client=http)
            result = await client.tags_import_rows(body)
            assert result is not None
            assert await Tag.objects.filter(name="from-client").aexists()
            exported = await client.tags_export(format="csv")
            assert "from-client" in exported


def test_typescript_sends_real_formdata_and_preserves_file_metadata(tmp_path):
    import shutil
    import subprocess

    npx, node = shutil.which("npx"), shutil.which("node")
    if not npx or not node:
        pytest.skip("requires Node and TypeScript")
    api, _urls = transfer_api("sync")
    source = tmp_path / "client.ts"
    source.write_text(generate_typescript(api.get_openapi_schema(path_prefix="")))
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
const {File} = require('node:buffer');
const {createClient} = require('./client.js');
const client = createClient({baseUrl: 'https://test', fetch: async (url, init) => {
  assert.equal(new URL(url).pathname, '/tags/import');
  assert.equal(new URL(url).searchParams.get('dry_run'), 'false');
  assert.ok(init.body instanceof FormData);
  assert.equal(new Headers(init.headers).has('Content-Type'), false);
  const file = init.body.get('file');
  assert.equal(file.name, 'tags.csv');
  assert.equal(file.type, 'text/csv');
  assert.equal(await file.text(), 'name\\nfrom-client\\n');
  return new Response(JSON.stringify({created: 1}), {status: 201,
    headers: {'Content-Type': 'application/json'}});
}});
(async () => {
  const result = await client.tagsImportRows({
    file: new File(['name\\nfrom-client\\n'], 'tags.csv', {type: 'text/csv'}),
  }, {dry_run: false}, {headers: {'Content-Type': 'application/json'}});
  assert.equal(result.created, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
    ran = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=10, check=False
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


@pytest.mark.parametrize("style", ["pydantic", "typeddict"])
def test_multipart_python_client_passes_strict_mypy(tmp_path, style):
    import subprocess
    import sys

    api, _urls = transfer_api("sync")
    generated(tmp_path, api, style)
    checked = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(tmp_path / "client.py")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
