import pytest
from django.contrib.auth.models import User
from ninja import Schema
from ninja.testing import TestClient

from ninja_devx.crud import (
    AutoCRUDController,
    BulkUpdateMixin,
    CRUDController,
    SoftDeleteMixin,
    model_schemas,
)
from ninja_devx.models import SoftDeletable, Stamped, TimeStamped, UserStamped
from tests.testapp.models import Document

pytestmark = pytest.mark.django_db


class DocumentOut(Schema):
    id: int
    title: str
    created_by_id: int | None
    updated_by_id: int | None


class DocumentIn(Schema):
    title: str


class Documents(
    SoftDeleteMixin[Document, DocumentOut],
    BulkUpdateMixin[Document, DocumentOut, DocumentIn],
    CRUDController[Document, DocumentOut, DocumentIn],
):
    pass


@pytest.fixture
def people():
    return User.objects.create(username="ada"), User.objects.create(username="bob")


def test_bases_compose_the_bookkeeping_columns():
    names = {field.name for field in Document._meta.get_fields()}
    columns = {"created_at", "updated_at", "created_by", "updated_by", "deleted_at", "deleted_by"}
    assert columns <= names
    assert issubclass(Stamped, TimeStamped)
    assert issubclass(Stamped, UserStamped)
    assert not Document._meta.get_field("created_by").editable
    assert issubclass(Document, SoftDeletable)


def test_create_and_update_stamp_the_request_user(people):
    ada, bob = people
    client = TestClient(Documents.as_router())
    created = client.post("/", json={"title": "plan"}, user=ada).json()
    document = Document.objects.get(pk=created["id"])
    assert (document.created_by, document.updated_by) == (ada, ada)
    assert document.created_at is not None
    assert document.updated_at >= document.created_at

    client.patch(f"/{document.pk}", json={"title": "plan v2"}, user=bob)
    document.refresh_from_db()
    assert (document.created_by, document.updated_by) == (ada, bob)
    assert document.title == "plan v2"


def test_anonymous_writes_leave_the_stamps_empty():
    client = TestClient(Documents.as_router())
    created = client.post("/", json={"title": "draft"}).json()
    assert (created["created_by_id"], created["updated_by_id"]) == (None, None)


def test_bulk_update_stamps_every_object(people):
    ada, bob = people
    client = TestClient(Documents.as_router())
    pks = [client.post("/", json={"title": f"d{i}"}, user=ada).json()["id"] for i in range(2)]
    client.post("/bulk-update", json={"pks": pks, "data": {"title": "same"}}, user=bob)
    assert list(Document.objects.filter(pk__in=pks).values_list("updated_by", flat=True)) == [
        bob.pk,
        bob.pk,
    ]


def test_soft_deletable_model_needs_no_configuration(people):
    ada, bob = people
    client = TestClient(Documents.as_router())
    pk = client.post("/", json={"title": "gone"}, user=ada).json()["id"]
    assert client.delete(f"/{pk}", user=bob).status_code == 204
    document = Document.objects.get(pk=pk)
    assert document.deleted_at is not None
    assert document.deleted_by == bob
    assert client.get(f"/{pk}").status_code == 404

    assert client.post(f"/{pk}/restore", user=ada).status_code == 200
    document.refresh_from_db()
    assert (document.deleted_at, document.deleted_by) == (None, None)


def test_generated_schemas_keep_the_stamps_out_of_the_input():
    output, input_ = model_schemas(Document)
    assert {"created_at", "updated_at", "created_by", "updated_by", "deleted_by"} <= set(
        output.model_fields
    )
    assert set(input_.model_fields) == {"title"}

    class AutoDocuments(AutoCRUDController[Document]):
        pass

    schema = AutoDocuments.input_schema()
    assert set(schema.model_fields) == {"title"}
