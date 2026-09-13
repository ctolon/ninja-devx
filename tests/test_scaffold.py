import importlib
import sys
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from mypy import api as mypy_api
from ninja.testing import TestClient

from ninja_devx.tooling.scaffold import ScaffoldOptions, render_resource, render_tests
from tests.testapp.models import Article, Note, Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / "generated_api"
    root.mkdir()
    (root / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield root
    for name in [m for m in sys.modules if m.startswith("generated_api")]:
        del sys.modules[name]


def load(package: Path, name: str, source: str):
    (package / f"{name}.py").write_text(source)
    return importlib.import_module(f"generated_api.{name}")


def test_generated_note_api_works(package):
    source = render_resource(Note, ScaffoldOptions(owner_field="owner"))
    assert 'status: Literal["draft", "done"] = "draft"' in source
    assert "deleted_at: datetime | None = None" in source
    assert 'owner_field = "owner"' in source

    module = load(package, "note", source)
    from ninja_devx.tooling.drift import schema_drift

    assert [d for d in schema_drift(module.NoteController) if d.severity != "info"] == []
    ada = User.objects.create(username="ada")
    client = TestClient(module.NoteController.as_router())
    created = client.post("/", json={"text": "generated"}, user=ada)
    assert created.status_code == 201
    assert created.json()["owner_id"] == ada.pk
    assert client.get("/?status=draft", user=ada).json()[0]["text"] == "generated"


def test_generated_article_api_handles_relations(package):
    source = render_resource(Article, ScaffoldOptions(owner_field="author"))
    assert "tags: list[int]" in source
    assert "title: Annotated[str, Field(max_length=200, min_length=1)]" in source
    assert (
        'slug: Annotated[str, Field(max_length=50, min_length=1, pattern="^[-a-zA-Z0-9_]+$")]'
        in source
    )
    assert "def resolve_tags(obj: Article) -> list[int]:" in source
    module = load(package, "article", source)

    ada = User.objects.create(username="ada")
    tag = Tag.objects.create(name="python")
    response = TestClient(module.ArticleController.as_router()).post(
        "/", json={"title": "T", "slug": "t", "tags": [tag.pk]}, user=ada
    )
    assert (response.status_code, response.json()["tags"]) == (201, [tag.pk])


def test_generated_code_type_checks(package):
    for model, name in ((Article, "article"), (Note, "note"), (Tag, "tag")):
        (package / f"{name}.py").write_text(render_resource(model))
    stdout, stderr, status = mypy_api.run(
        [str(package), "--strict", "--no-incremental", "--follow-imports=silent"]
    )
    assert status == 0, stdout + stderr


def test_generated_tests_are_valid_python():
    source = render_tests(Tag, "generated_api.tag")
    compile(source, "test_tag_api.py", "exec")
    assert "def test_tag_missing_is_404(ninja_client: Callable[..., TestClient]) -> None:" in source


def test_field_selection(package):
    source = render_resource(Tag, ScaffoldOptions(read_fields=["id"], write_fields=["name"]))
    assert "class TagOut(Schema):\n    id: int\n" in source
    with pytest.raises(ValueError, match="no fields"):
        render_resource(Tag, ScaffoldOptions(read_fields=["missing"]))


def test_command_writes_files_and_refuses_to_overwrite(tmp_path):
    output = tmp_path / "api" / "tag.py"
    tests = tmp_path / "tests" / "test_tag_api.py"
    call_command("devx_scaffold", "testapp.Tag", "--output", str(output), "--tests", str(tests))
    assert "class TagController" in output.read_text()
    assert tests.exists()
    assert (output.parent / "__init__.py").exists()

    with pytest.raises(CommandError, match="Refusing to overwrite"):
        call_command("devx_scaffold", "testapp.Tag", "--output", str(output), "--tests", str(tests))
    call_command("devx_scaffold", "testapp.Tag", "--output", str(output), "--no-tests", "--force")


def test_command_errors(capsys):
    with pytest.raises(CommandError, match="Unknown model"):
        call_command("devx_scaffold", "testapp.Nope")
    call_command("devx_scaffold", "testapp.Tag", "--print", "--async")
    assert "AsyncCRUDController[Tag, TagOut, TagIn]" in capsys.readouterr().out
