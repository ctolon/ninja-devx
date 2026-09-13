import pytest
from django.test import override_settings
from ninja import Schema
from ninja.testing import TestClient

from ninja_devx.contrib.audit.log import AuditMixin, record
from ninja_devx.contrib.audit.models import AuditEntry
from ninja_devx.crud import BulkCreateMixin, CRUDController, ModelRepository
from ninja_devx.layers import ValidationFailed
from tests.testapp.models import Tag

pytestmark = pytest.mark.django_db(databases=["default", "other"])


class OtherDatabaseRouter:
    def db_for_read(self, model, **hints):
        return "other"

    def db_for_write(self, model, **hints):
        return "other"


class TagIn(Schema):
    name: str


class TagOut(TagIn):
    id: int


class Tags(
    AuditMixin[Tag], BulkCreateMixin[Tag, TagOut, TagIn], CRUDController[Tag, TagOut, TagIn]
):
    pass


@override_settings(DATABASE_ROUTERS=[OtherDatabaseRouter()])
def test_bulk_and_audit_use_the_write_transaction():
    client = TestClient(Tags.as_router())
    failed = client.post("/bulk", json=[{"name": "same"}, {"name": "same"}])
    assert failed.status_code == 422
    assert Tag.objects.using("other").count() == 0
    assert AuditEntry.objects.using("other").count() == 0
    result = client.post("/bulk", json=[{"name": "one"}, {"name": "two"}])
    assert result.status_code == 201
    assert Tag.objects.using("other").count() == 2
    assert AuditEntry.objects.using("other").count() == 2
    assert Tag.objects.using("default").count() == 0
    assert AuditEntry.objects.using("default").count() == 0


@override_settings(DATABASE_ROUTERS=[OtherDatabaseRouter()])
def test_repository_transaction_follows_write_router():
    repository = ModelRepository(Tag)

    def duplicate_write():
        with repository.transaction():
            repository.add({"name": "duplicate"})
            repository.add({"name": "duplicate"})

    with pytest.raises(ValidationFailed):
        duplicate_write()
    assert Tag.objects.using("other").count() == 0


def test_audit_explicit_object_database_overrides_default_router():
    instance = Tag.objects.using("other").create(name="other")
    entry = record(None, "export", instance)
    assert entry._state.db == "other"
    assert entry.content_type.app_label == "testapp"
    assert AuditEntry.objects.using("default").count() == 0


@pytest.mark.parametrize("selection", ["queryset", "operation"])
def test_explicit_alias_covers_crud_bulk_audit_and_import(selection):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from ninja_devx import ETag
    from ninja_devx.crud.transfer import ImportMixin

    class ExplicitTags(ImportMixin[Tag, TagIn], Tags):
        etag = ETag(require_if_match=True)

        def get_queryset(self, request):
            queryset = Tag.objects.all()
            return queryset.using("other") if selection == "queryset" else queryset

    options = {"database": "other"} if selection == "operation" else {}
    client = TestClient(ExplicitTags.as_router(**options))
    created = client.post("/", json={"name": "explicit"})
    assert created.status_code == 201
    pk = created.json()["id"]
    fetched = client.get(f"/{pk}")
    assert fetched.status_code == 200
    changed = client.put(f"/{pk}", json={"name": "changed"}, headers={"If-Match": fetched["ETag"]})
    assert changed.status_code == 200
    assert client.post("/bulk", json=[{"name": "bulk"}]).status_code == 201
    dry = client.post(
        "/import?dry_run=true",
        FILES={"file": SimpleUploadedFile("tags.csv", b"name\ndry\n")},
    )
    assert dry.status_code == 200
    assert not Tag.objects.using("other").filter(name="dry").exists()
    assert AuditEntry.objects.using("other").count() == 3
    deleted = client.delete(f"/{pk}", headers={"If-Match": changed["ETag"]})
    assert deleted.status_code == 204
    assert Tag.objects.using("default").count() == 0
    assert AuditEntry.objects.using("default").count() == 0


def test_custom_repository_alias_mismatch_is_rejected_before_write():
    from ninja_devx.crud import ModelService
    from ninja_devx.exceptions import ControllerConfigError

    class WrongService(ModelService[Tag]):
        def __init__(self):
            super().__init__(ModelRepository(Tag, using="default"))

    class WrongTags(Tags):
        service_class = WrongService

    with pytest.raises(ControllerConfigError, match="repository uses"):
        TestClient(WrongTags.as_router(database="other")).post("/", json={"name": "wrong"})
    assert Tag.objects.using("default").count() == 0
    assert Tag.objects.using("other").count() == 0


@pytest.mark.parametrize("route", ["/", "/bulk", "/import"])
def test_custom_create_cannot_commit_outside_queryset_scope(route):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from ninja_devx.crud.transfer import ImportMixin

    class ScopedTags(ImportMixin[Tag, TagIn], Tags):
        refresh_after_write = False

        def get_queryset(self, request):
            return Tag.objects.using("other").filter(name__startswith="allowed")

    client = TestClient(ScopedTags.as_router())
    if route == "/import":
        response = client.post(
            route, FILES={"file": SimpleUploadedFile("tags.csv", b"name\nforbidden\n")}
        )
    else:
        payload = {"name": "forbidden"}
        response = client.post(route, json=[payload] if route == "/bulk" else payload)
    assert response.status_code == 404
    assert Tag.objects.using("other").count() == 0
    assert AuditEntry.objects.using("other").count() == 0
