import pytest
from django.contrib.auth.models import User
from ninja import Schema
from ninja.pagination import PageNumberPagination
from ninja.testing import TestAsyncClient

from ninja_devx.crud import (
    AsyncCRUDController,
    AsyncReadOnlyModelController,
    CRUDController,
    SoftDeleteMixin,
)
from tests.testapp.models import Article, Note

pytestmark = pytest.mark.django_db(transaction=True)


class UserOut(Schema):
    id: int
    username: str


class TagOut(Schema):
    id: int
    name: str


class ArticleDetail(Schema):
    id: int
    title: str
    author: UserOut
    tags: list[TagOut]


class NoteOut(Schema):
    id: int
    text: str
    owner_id: int


class NoteIn(Schema):
    text: str


class AsyncNotes(AsyncCRUDController[Note, NoteOut, NoteIn]):
    owner_field = "owner"
    search_fields = ("text",)


class AsyncArticles(AsyncReadOnlyModelController[Article, ArticleDetail]):
    pagination_class = PageNumberPagination


async def test_async_crud_round_trip():
    ada = await User.objects.acreate(username="ada")
    client = TestAsyncClient(AsyncNotes.as_router())

    created = await client.post("/", json={"text": "async note"}, user=ada)
    assert created.status_code == 201
    pk = created.json()["id"]

    assert (await client.get(f"/{pk}", user=ada)).json()["text"] == "async note"
    assert [n["text"] for n in (await client.get("/?search=async", user=ada)).json()] == [
        "async note"
    ]

    patched = await client.patch(f"/{pk}", json={"text": "patched"}, user=ada)
    assert patched.json()["text"] == "patched"
    replaced = await client.put(f"/{pk}", json={"text": "replaced"}, user=ada)
    assert replaced.json()["text"] == "replaced"

    assert (await client.delete(f"/{pk}", user=ada)).status_code == 204
    assert (await client.get(f"/{pk}", user=ada)).status_code == 404


async def test_async_crud_enforces_ownership():
    ada = await User.objects.acreate(username="ada")
    bob = await User.objects.acreate(username="bob")
    note = await Note.objects.acreate(owner=ada, text="private")
    client = TestAsyncClient(AsyncNotes.as_router())
    assert (await client.get(f"/{note.pk}", user=bob)).status_code == 403
    assert (await client.get("/")).status_code == 401


async def test_async_list_serializes_prefetched_relations_with_pagination():
    ada = await User.objects.acreate(username="ada")
    for index in range(3):
        await Article.objects.acreate(title=f"A{index}", slug=f"a{index}", author=ada)
    page = (await TestAsyncClient(AsyncArticles.as_router()).get("/?page=1")).json()
    assert page["count"] == 3
    assert page["items"][0]["author"]["username"] == "ada"


def test_async_and_sync_controllers_share_operation_ids():
    class SyncNotes(CRUDController[Note, NoteOut, NoteIn]):
        owner_field = "owner"

    def operation_ids(router):
        return sorted(
            op.operation_id.split("_", 2)[-1]
            for view in router.path_operations.values()
            for op in view.operations
        )

    assert operation_ids(AsyncNotes.as_router()) == operation_ids(SyncNotes.as_router())


async def test_async_writes_hop_to_a_thread_once():
    from ninja_devx.testing.clients import assert_max_hops

    ada = await User.objects.acreate(username="ada")
    note = await Note.objects.acreate(owner=ada, text="hop")
    client = TestAsyncClient(AsyncNotes.as_router())
    with assert_max_hops(1) as hops:
        created = await client.post("/", json={"text": "one hop"}, user=ada)
    assert created.status_code == 201
    assert hops.functions == ["CreateMixin._create"]
    with assert_max_hops(1):  # lookup, object permissions, write and reload together
        patched = await client.patch(f"/{note.pk}", json={"text": "patched"}, user=ada)
    assert patched.status_code == 200


class AsyncSoftNotes(SoftDeleteMixin[Note, NoteOut], AsyncCRUDController[Note, NoteOut, NoteIn]):
    pass


@pytest.mark.django_db(transaction=True)
async def test_async_soft_delete_and_restore():
    user = await User.objects.acreate(username="async-owner")
    note = await Note.objects.acreate(owner=user, text="x")
    client = TestAsyncClient(AsyncSoftNotes.as_router())
    assert (await client.delete(f"/{note.pk}")).status_code == 204
    assert (await client.post(f"/{note.pk}/restore")).status_code == 200
    await note.arefresh_from_db()
    assert note.deleted_at is None
