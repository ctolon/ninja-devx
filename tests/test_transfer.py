import csv
import io
import json
from typing import Annotated

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import IsStaff
from ninja_devx.contrib.audit.log import AuditMixin
from ninja_devx.contrib.audit.models import AuditEntry
from ninja_devx.crud import CRUDController
from ninja_devx.crud.transfer import ExportMixin, ImportMixin
from ninja_devx.serialization.visibility import VisibleTo
from tests.testapp.models import Note, Tag

pytestmark = pytest.mark.django_db


class NoteIn(Schema):
    text: str
    priority: int = 0


class NoteOut(Schema):
    id: int
    text: str
    priority: Annotated[int | None, VisibleTo(IsStaff())] = None


class Notes(
    ExportMixin[Note, NoteOut],
    ImportMixin[Note, NoteIn],
    AuditMixin[Note],
    CRUDController[Note, NoteOut, NoteIn],
):
    owner_field = "owner"
    scope_queryset_to_owner = True
    filter_fields = {"priority": ("gte",)}
    ordering_fields = ("priority", "id")


class TagIn(Schema):
    name: str


class TagOut(TagIn):
    id: int


class AsyncTags(
    ExportMixin[Tag, TagOut], ImportMixin[Tag, TagIn], CRUDController[Tag, TagOut, TagIn]
):
    mode = "async"
    export_formats = ("jsonl",)


@pytest.fixture
def ada():
    return User.objects.create(username="ada", is_staff=True)


def upload(name, content):
    return {"file": SimpleUploadedFile(name, content.encode())}


def body(response):
    return response.content.decode()


def test_export_csv_respects_filters_ordering_scope_and_visibility(ada):
    bob = User.objects.create(username="bob")
    Note.objects.create(owner=ada, text="=SUM(A1)", priority=1)
    Note.objects.create(owner=ada, text="b", priority=5)
    Note.objects.create(owner=bob, text="not mine", priority=9)
    client = TestClient(Notes.as_router())
    response = client.get("/export?format=csv&priority__gte=1&ordering=-priority", user=ada)
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == 'attachment; filename="notes.csv"'
    rows = list(csv.DictReader(io.StringIO(body(response))))
    assert [(r["text"], r["priority"]) for r in rows] == [("b", "5"), ("'=SUM(A1)", "1")]

    ada.is_staff = False
    ada.save()
    hidden = list(csv.DictReader(io.StringIO(body(client.get("/export", user=ada)))))
    assert {r["priority"] for r in hidden} == {""}


def test_export_jsonl_and_unknown_format(ada):
    Note.objects.create(owner=ada, text="a", priority=2)
    client = TestClient(Notes.as_router())
    lines = body(client.get("/export?format=jsonl", user=ada)).splitlines()
    assert json.loads(lines[0])["text"] == "a"
    assert client.get("/export?format=xml", user=ada).status_code == 422


def test_import_csv_creates_through_perform_create(ada):
    client = TestClient(Notes.as_router())
    response = client.post(
        "/import", FILES=upload("notes.csv", "text,priority\nhello,3\nworld,\n"), user=ada
    )
    assert response.status_code == 201, response.json()
    assert response.json() == {"created": 2, "dry_run": False}
    assert list(Note.objects.order_by("id").values_list("text", "priority", "owner")) == [
        ("hello", 3, ada.pk),
        ("world", 0, ada.pk),
    ]
    assert AuditEntry.objects.filter(action="create").count() == 2


def test_import_is_all_or_nothing_with_row_errors(ada):
    client = TestClient(Notes.as_router())
    response = client.post(
        "/import", FILES=upload("notes.csv", "text,priority\nok,1\nbad,high\n"), user=ada
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["file", 3, "priority"]
    assert not Note.objects.exists()


def test_dry_run_and_jsonl(ada):
    client = TestClient(Notes.as_router())
    content = '{"text": "a"}\n\n{"text": "b", "priority": 2}\n'
    dry = client.post("/import?dry_run=true", FILES=upload("n.jsonl", content), user=ada)
    assert (dry.status_code, dry.json()) == (200, {"created": 2, "dry_run": True})
    assert not Note.objects.exists()
    bad = client.post("/import", FILES=upload("n.jsonl", "[1]\n"), user=ada)
    assert bad.json()["detail"][0]["loc"] == ["file", 1]


def test_row_limit(ada, monkeypatch):
    monkeypatch.setattr(Notes, "max_import_rows", 1)
    client = TestClient(Notes.as_router())
    assert (
        client.post("/import", FILES=upload("n.csv", "text\na\nb\n"), user=ada).status_code == 413
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_export_and_import():
    client = TestAsyncClient(AsyncTags.as_router())
    created = await client.post(
        "/import", FILES=upload("t.jsonl", '{"name": "x"}\n{"name": "y"}\n')
    )
    assert created.status_code == 201
    response = await client.get("/export?format=jsonl")
    names = [json.loads(line)["name"] for line in body(response).splitlines()]
    assert sorted(names) == ["x", "y"]


def test_openapi(ada):
    from ninja import NinjaAPI

    api = NinjaAPI(urls_namespace="transfer")
    api.add_router("/notes", Notes.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]
    assert "text/csv" in paths["/notes/export"]["get"]["responses"]["200"]["content"]
    assert "multipart/form-data" in paths["/notes/import"]["post"]["requestBody"]["content"]


@pytest.mark.parametrize("filename", ["bad.csv", "bad.jsonl"])
def test_invalid_utf8_returns_validation_error_and_writes_nothing(ada, filename):
    file = SimpleUploadedFile(filename, b"text\n\xff\n")
    response = TestClient(Notes.as_router()).post("/import", FILES={"file": file}, user=ada)
    assert response.status_code == 422
    assert Note.objects.count() == 0


def test_import_byte_limit_precedes_parsing_and_writes(ada):
    class SmallImports(Notes):
        max_import_bytes = 8

    file = SimpleUploadedFile("huge.csv", b"text\nvery large row\n")
    response = TestClient(SmallImports.as_router()).post("/import", FILES={"file": file}, user=ada)
    assert response.status_code == 413
    assert Note.objects.count() == 0
