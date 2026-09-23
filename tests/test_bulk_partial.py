import pytest
from ninja import Schema
from ninja.testing import TestClient

from ninja_devx.crud import BulkCreateMixin, BulkUpdateMixin, CRUDController
from tests.testapp.models import Tag

pytestmark = pytest.mark.django_db


class TagIn(Schema):
    name: str


class TagOut(Schema):
    id: int
    name: str


class BulkTagsPartial(
    BulkCreateMixin[Tag, TagOut, TagIn],
    BulkUpdateMixin[Tag, TagOut, TagIn],
    CRUDController[Tag, TagOut, TagIn],
):
    bulk_partial = True


class BulkTagsAllOrNothing(
    BulkCreateMixin[Tag, TagOut, TagIn],
    BulkUpdateMixin[Tag, TagOut, TagIn],
    CRUDController[Tag, TagOut, TagIn],
):
    pass


def test_bulk_create_partial_success_reports_each_item():
    Tag.objects.create(name="a")
    client = TestClient(BulkTagsPartial.as_router())

    response = client.post("/bulk", json=[{"name": "c"}, {"name": "a"}])

    assert response.status_code == 207
    assert response.headers["X-Bulk-Failed"] == "1"
    results = response.json()["results"]
    assert [entry["index"] for entry in results] == [0, 1]
    assert results[0]["status"] == 201
    assert results[0]["data"]["name"] == "c"
    assert results[1]["status"] == 422
    assert results[1]["errors"]
    assert set(Tag.objects.values_list("name", flat=True)) == {"a", "c"}


def test_bulk_create_partial_all_success_still_returns_207_when_any_would_fail():
    client = TestClient(BulkTagsPartial.as_router())

    response = client.post("/bulk", json=[{"name": "x"}, {"name": "y"}])

    assert response.status_code == 207
    assert response.headers["X-Bulk-Failed"] == "0"
    results = response.json()["results"]
    assert [entry["status"] for entry in results] == [201, 201]
    assert set(Tag.objects.values_list("name", flat=True)) == {"x", "y"}


def test_bulk_create_default_is_still_all_or_nothing():
    Tag.objects.create(name="a")
    client = TestClient(BulkTagsAllOrNothing.as_router())

    response = client.post("/bulk", json=[{"name": "c"}, {"name": "a"}])

    assert response.status_code == 422
    assert "X-Bulk-Failed" not in response.headers
    assert set(Tag.objects.values_list("name", flat=True)) == {"a"}


def test_bulk_update_partial_reports_unknown_pks_and_failures():
    keep = Tag.objects.create(name="keep")
    rename = Tag.objects.create(name="rename-me")
    client = TestClient(BulkTagsPartial.as_router())

    response = client.post(
        "/bulk-update",
        json={"pks": [rename.pk, 999, keep.pk], "data": {"name": "keep"}},
    )

    assert response.status_code == 207
    assert response.headers["X-Bulk-Failed"] == "2"
    results = response.json()["results"]
    by_index = {entry["index"]: entry for entry in results}
    assert by_index[0]["status"] == 422  # duplicate name "keep"
    assert by_index[1]["status"] == 404
    assert by_index[2]["status"] == 200
    assert by_index[2]["data"]["name"] == "keep"
    rename.refresh_from_db()
    assert rename.name == "rename-me"


def test_bulk_update_partial_success_returns_updated_items():
    first = Tag.objects.create(name="one")
    second = Tag.objects.create(name="two")
    client = TestClient(BulkTagsPartial.as_router())

    response = client.post(
        "/bulk-update", json={"pks": [first.pk, second.pk], "data": {"name": "same"}}
    )

    assert response.status_code == 207
    assert response.headers["X-Bulk-Failed"] == "1"  # the second rename collides with the first
    results = response.json()["results"]
    assert results[0]["status"] == 200
    assert results[1]["status"] == 422
