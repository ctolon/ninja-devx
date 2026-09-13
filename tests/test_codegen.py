import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from django.contrib.auth.models import User
from django.core.handlers.wsgi import WSGIHandler
from django.core.management import CommandError, call_command
from mypy import api as mypy_api

from ninja_devx.codegen import generate_python, generate_typescript, read_operations
from tests.urls import api

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module")
def document():
    return api.get_openapi_schema()


def load_client(tmp_path: Path, source: str):
    path = tmp_path / "client.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("generated_client", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generated_client"] = module
    spec.loader.exec_module(module)
    return module


def test_operations_are_read_from_the_document(document):
    operations = {operation.operation_id: operation for operation in read_operations(document)}
    retrieve = operations["article_controller_retrieve"]
    assert (retrieve.method, retrieve.path) == ("GET", "/api/articles/{pk}")
    assert [parameter.name for parameter in retrieve.parameters] == ["pk"]
    assert operations["article_controller_create"].body is not None


@pytest.mark.django_db(transaction=True)
def test_python_client_end_to_end(tmp_path, document):
    module = load_client(tmp_path, generate_python(document))
    User.objects.create(username="ada")
    http = httpx.Client(
        transport=httpx.WSGITransport(app=WSGIHandler()), base_url="http://testserver"
    )
    client = module.ApiClient(client=http)
    http.headers["X-User"] = "ada"

    created = client.article_controller_create(module.ArticleIn(title="Hello", slug="hello"))
    assert isinstance(created, module.ArticleOut)
    assert created.title == "Hello"
    page = client.article_controller_list(search="hell", ordering=["title"])
    assert (page.count, page.items[0].slug) == (1, "hello")
    patched = client.article_controller_partial_update(created.id, module.ArticleInPatch(body="x"))
    assert (patched.title, patched.body) == ("Hello", "x")  # unset fields are not sent
    assert client.ping_controller_ping() == {"ok": True}

    with pytest.raises(module.ApiError) as exc_info:
        client.article_controller_retrieve(999)
    assert exc_info.value.status_code == 404
    client.article_controller_destroy(created.id)


NULL = {"type": "null"}


def test_pydantic_models_carry_constraints_and_formats():
    source = generate_python(
        {
            "openapi": "3.1.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "Thing": {
                        "type": "object",
                        "description": "A thing.",
                        "required": ["name", "class", "when"],
                        "properties": {
                            "name": {"type": "string", "maxLength": 20, "pattern": "^[a-z]+$"},
                            "class": {"type": "integer", "minimum": 0},
                            "when": {"type": "string", "format": "date-time"},
                            "ref": {
                                "anyOf": [{"type": "string", "format": "uuid"}, {"type": "null"}]
                            },
                            "size": {
                                "anyOf": [{"type": "integer", "maximum": 9}, {"type": "null"}]
                            },
                        },
                    }
                }
            },
        }
    )
    assert 'name: Annotated[str, _Field(max_length=20, pattern="^[a-z]+$")]' in source
    assert 'class_: Annotated[int, _Field(alias="class", ge=0)]' in source
    assert "when: _dt.datetime" in source
    assert "ref: _uuid.UUID | None = None" in source
    assert "size: Annotated[int | None, _Field(le=9)] = None" in source
    assert '"A thing."' in source


@pytest.mark.django_db(transaction=True)
def test_typeddict_client_end_to_end(tmp_path, document):
    module = load_client(tmp_path, generate_python(document, style="typeddict"))
    User.objects.create(username="ada")
    http = httpx.Client(
        transport=httpx.WSGITransport(app=WSGIHandler()), base_url="http://testserver"
    )
    client = module.ApiClient(client=http)
    http.headers["X-User"] = "ada"

    created = client.article_controller_create({"title": "Hello", "slug": "hello"})
    assert created["title"] == "Hello"
    page = client.article_controller_list(search="hell", ordering=["title"])
    assert page["count"] == 1
    assert client.article_controller_retrieve(created["id"])["slug"] == "hello"
    assert client.ping_controller_ping() == {"ok": True}

    with pytest.raises(module.ApiError) as exc_info:
        client.article_controller_retrieve(999)
    assert exc_info.value.status_code == 404
    client.article_controller_destroy(created["id"])


@pytest.mark.parametrize("style", ["pydantic", "typeddict"])
def test_python_client_type_checks(tmp_path, document, style):
    path = tmp_path / "client.py"
    path.write_text(generate_python(document, style=style))
    stdout, stderr, status = mypy_api.run([str(path), "--strict", "--no-incremental"])
    assert status == 0, stdout + stderr


def test_typescript_client(tmp_path, document):
    source = generate_typescript(document)
    assert "export interface ArticleOut {" in source
    assert (
        "articleControllerRetrieve: (pk: number, requestOptions: RequestOptions = {}) =>" in source
    )
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx is not installed")
    path = tmp_path / "client.ts"
    path.write_text(source)
    result = subprocess.run(
        [
            npx,
            "-y",
            "-p",
            "typescript@5.9.3",
            "tsc",
            "--noEmit",
            "--strict",
            "--target",
            "es2022",
            "--lib",
            "es2022,dom",
            "--skipLibCheck",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if "ENOTFOUND" in result.stderr or "EAI_AGAIN" in result.stderr:
        pytest.skip("npm registry unreachable")
    assert result.returncode == 0, result.stdout + result.stderr


def test_openapi_command(tmp_path, capsys):
    call_command("devx_openapi", "tests.urls.api")
    assert json.loads(capsys.readouterr().out)["info"]["title"] == "Test API"

    output = tmp_path / "client.ts"
    call_command(
        "devx_openapi", "tests.urls.api", "--format", "typescript", "--output", str(output)
    )
    call_command(
        "devx_openapi",
        "tests.urls.api",
        "--format",
        "typescript",
        "--output",
        str(output),
        "--check",
    )
    output.write_text("stale")
    with pytest.raises(CommandError, match="outdated"):
        call_command(
            "devx_openapi",
            "tests.urls.api",
            "--format",
            "typescript",
            "--output",
            str(output),
            "--check",
        )
    with pytest.raises(CommandError, match="not a NinjaAPI"):
        call_command("devx_openapi", "tests.urls.urlpatterns")


def test_command_python_styles(tmp_path):
    for style, marker in (("pydantic", "(_BaseModel):"), ("typeddict", "(TypedDict):")):
        output = tmp_path / f"{style}.py"
        call_command(
            "devx_openapi", "tests.urls.api", "--format", "python",
            "--python-style", style, "--output", str(output),
        )  # fmt: skip
        assert marker in output.read_text()
