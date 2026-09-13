import pytest
from django.contrib.auth.models import User
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.crud import CursorPagination, ReadOnlyModelController
from tests.testapp.models import Note

pytestmark = pytest.mark.django_db


class NoteOut(Schema):
    id: int
    priority: int


class Notes(ReadOnlyModelController[Note, NoteOut]):
    pagination_class = CursorPagination
    pagination_options = {"page_size": 2}
    ordering_fields = ("priority", "id")
    default_ordering = ("-priority",)


def make_notes():
    ada = User.objects.create(username="ada")
    # duplicates in the ordering field exercise the offset logic
    return [Note.objects.create(owner=ada, text=str(i), priority=i // 2) for i in range(7)]


def cursor_of(url):
    return url.split("cursor=")[1].split("&")[0]


def walk(client, query=""):
    """Follow ``next`` links (Ninja's test request has no query string to preserve)."""
    ids, pages, url = [], 0, f"/?{query}"
    while url:
        page = client.get(url).json()
        assert "results" in page, (url, page)
        ids.extend(item["id"] for item in page["results"])
        url = page["next"] and f"/?{query}&cursor={cursor_of(page['next'])}"
        pages += 1
    return ids, pages


def test_cursor_follows_default_ordering_with_pk_tiebreaker():
    notes = make_notes()
    ids, pages = walk(TestClient(Notes.as_router()))
    expected = [n.pk for n in sorted(notes, key=lambda n: (-n.priority, -n.pk))]
    assert ids == expected
    assert pages == 4


def test_cursor_follows_client_ordering():
    notes = make_notes()
    ids, _ = walk(TestClient(Notes.as_router()), "ordering=priority")
    assert ids == [n.pk for n in sorted(notes, key=lambda n: (n.priority, n.pk))]


def test_cursor_rejects_another_ordering():
    make_notes()
    client = TestClient(Notes.as_router())
    next_url = client.get("/?ordering=priority").json()["next"]
    response = client.get(f"/?ordering=-priority&cursor={cursor_of(next_url)}")
    assert response.status_code == 422
    assert "different ordering" in str(response.json())


def test_cursor_ordering_fields_must_not_be_nullable():
    class Wrong(ReadOnlyModelController[Note, NoteOut]):
        pagination_class = CursorPagination
        ordering_fields = ("deleted_at",)

    with pytest.raises(ControllerConfigError, match="nullable fields"):
        Wrong.as_router()


@pytest.mark.django_db(transaction=True)
async def test_async_cursor_pagination():
    ada = await User.objects.acreate(username="ada")
    for i in range(5):
        await Note.objects.acreate(owner=ada, text=str(i), priority=i)

    class AsyncNotes(Notes):
        mode = "async"

    client = TestAsyncClient(AsyncNotes.as_router())
    first = (await client.get("/")).json()
    assert [item["priority"] for item in first["results"]] == [4, 3]
    second = (await client.get(f"/?cursor={cursor_of(first['next'])}")).json()
    assert [item["priority"] for item in second["results"]] == [2, 1]


# --- limit/offset ---------------------------------------------------------------------


from ninja_devx.crud import LimitOffsetPagination  # noqa: E402


class OffsetNotes(ReadOnlyModelController[Note, NoteOut]):
    pagination_class = LimitOffsetPagination
    pagination_options = {"limit": 3, "max_limit": 4, "max_offset": 6}
    ordering_fields = ("priority",)


class UncountedNotes(OffsetNotes):
    pagination_options = {"limit": 3, "count": False}


def test_offset_pages_are_stable_and_linked():
    notes = make_notes()  # priorities repeat: the pk tiebreaker keeps pages disjoint
    client = TestClient(OffsetNotes.as_router())
    first = client.get("/?ordering=priority").json()
    second = client.get("/?ordering=priority&offset=3").json()
    assert first["count"] == 7
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert ids == [n.pk for n in sorted(notes, key=lambda n: (n.priority, n.pk))][:6]
    assert "offset=3" in first["next"]
    assert first["previous"] is None
    assert "offset=0" in second["previous"]


def test_offset_limits():
    make_notes()
    client = TestClient(OffsetNotes.as_router())
    assert len(client.get("/?limit=100").json()["items"]) == 4  # clamped to max_limit
    too_deep = client.get("/?offset=7")
    assert too_deep.status_code == 422
    assert "cursor pagination" in str(too_deep.json())


def test_offset_without_count():
    make_notes()
    client = TestClient(UncountedNotes.as_router())
    page = client.get("/?offset=3").json()
    assert page["count"] is None
    assert page["next"] is not None
    assert client.get("/?offset=6").json()["next"] is None


@pytest.mark.django_db(transaction=True)
async def test_async_offset_pagination():
    ada = await User.objects.acreate(username="ada")
    for i in range(5):
        await Note.objects.acreate(owner=ada, text=str(i), priority=i)

    class AsyncOffsetNotes(OffsetNotes):
        mode = "async"

    page = (await TestAsyncClient(AsyncOffsetNotes.as_router()).get("/?ordering=-priority")).json()
    assert [item["priority"] for item in page["items"]] == [4, 3, 2]
    assert page["count"] == 5


def test_links_keep_other_query_parameters():
    from ninja_devx.crud.pagination import _with_query

    url = _with_query("https://api.test/notes/?ordering=priority&offset=3&q=x", limit=3, offset=6)
    assert url == "https://api.test/notes/?ordering=priority&q=x&limit=3&offset=6"
