import pytest
from django.contrib.auth.models import User
from django.db.models import QuerySet
from django.http import HttpRequest
from django.test import override_settings
from ninja import FilterSchema, NinjaAPI, Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import (
    Also,
    Container,
    ControllerConfigError,
    ControllerOptions,
    DenyAll,
    IsReadOnly,
    IsStaff,
    post,
)
from ninja_devx.crud import (
    AsyncCRUDController,
    AsyncReadOnlyModelController,
    CRUDController,
    Instance,
    Locked,
    ReadOnlyModelController,
)
from ninja_devx.security.auth import arequest_user, request_user
from ninja_devx.security.permissions import as_permission
from tests.testapp.models import Article, Comment, Note

pytestmark = pytest.mark.django_db


class NoteOut(Schema):
    id: int
    text: str
    owner_id: int


class NoteIn(Schema):
    text: str


class CommentOut(Schema):
    id: int
    body: str


class CommentIn(Schema):
    body: str


@pytest.fixture
def ada():
    return User.objects.create(username="ada")


@pytest.fixture
def bob():
    return User.objects.create(username="bob")


def test_also_adds_to_controller_permissions(ada):
    class Guarded(CRUDController[Note, NoteOut, NoteIn]):
        owner_field = "owner"

        @post("/{pk}/archive", response=NoteOut, permissions=Also(IsStaff()))
        def archive(self, request: HttpRequest, note: Instance[Note]) -> Note:
            return note

    note = Note.objects.create(owner=ada, text="mine")
    client = TestClient(Guarded.as_router())
    assert (
        client.post(f"/{note.pk}/archive").status_code == 401
    )  # controller's IsAuthenticated kept
    assert client.post(f"/{note.pk}/archive", user=ada).status_code == 403  # plus IsStaff
    ada.is_staff = True
    assert client.post(f"/{note.pk}/archive", user=ada).status_code == 200


def test_read_only_permission(ada):
    class Public(CRUDController[Note, NoteOut, NoteIn]):
        options = ControllerOptions(permissions=[IsReadOnly()])

    client = TestClient(Public.as_router())
    assert client.get("/").status_code == 200
    assert client.post("/", json={"text": "x"}).status_code == 403


def test_policies_as_object_permissions(ada, bob):
    class AuthorOnly:
        def allows(self, subject: object, obj: Note) -> bool:
            return obj.owner_id == getattr(subject, "pk", None)

    class PolicyNotes(ReadOnlyModelController[Note, NoteOut]):
        options = ControllerOptions(
            permissions=[as_permission(AuthorOnly(), request_user, asubject=arequest_user)]
        )

    note = Note.objects.create(owner=ada, text="mine")
    client = TestClient(PolicyNotes.as_router())
    assert client.get(f"/{note.pk}", user=ada).status_code == 200
    assert client.get(f"/{note.pk}", user=bob).status_code == 403


class CommentOwnerOut(Schema):
    id: int
    body: str


def test_owner_field_can_follow_relations(ada, bob):
    class ArticleComments(ReadOnlyModelController[Comment, CommentOwnerOut]):
        owner_field = "article__author"
        scope_queryset_to_owner = True

    article = Article.objects.create(title="A", slug="a", author=ada)
    comment = Comment.objects.create(article=article, body="hi")
    client = TestClient(ArticleComments.as_router())
    assert client.get(f"/{comment.pk}", user=ada).status_code == 200
    assert client.get("/", user=ada).json() == [{"id": comment.pk, "body": "hi"}]
    assert client.get(f"/{comment.pk}", user=bob).status_code == 404  # scoped away


def test_owner_field_errors_at_startup():
    class Misspelled(ReadOnlyModelController[Note, NoteOut]):
        owner_field = "ownr"

    with pytest.raises(ControllerConfigError, match="no field 'ownr'"):
        Misspelled.as_router()

    class Nested(CRUDController[Comment, CommentOut, CommentIn]):
        owner_field = "article__author"

    with pytest.raises(ControllerConfigError, match="override perform_create"):
        Nested.as_router()


def test_locked_instances(ada):
    class Locking(CRUDController[Note, NoteOut, NoteIn]):
        @post("/{pk}/touch", response=NoteOut, atomic=True)
        def touch(self, request: HttpRequest, note: Locked[Note]) -> Note:
            note.text = "touched"
            note.save(update_fields=["text"])
            return note

    note = Note.objects.create(owner=ada, text="x")
    assert TestClient(Locking.as_router()).post(f"/{note.pk}/touch").json()["text"] == "touched"


class NoteFilters(FilterSchema):
    text: str | None = None


class LongNotes:
    def __call__(self, filters: FilterSchema) -> QuerySet[Note]:
        return filters.filter(Note.objects.filter(text__startswith="long"))


def test_selector_backed_lists(ada):
    class SelectedNotes(ReadOnlyModelController[Note, NoteOut]):
        selector_class = LongNotes
        filter_schema = NoteFilters
        ordering_fields = ("id",)

    Note.objects.create(owner=ada, text="long one")
    Note.objects.create(owner=ada, text="short")
    Note.objects.create(owner=ada, text="long two")
    client = TestClient(SelectedNotes.as_router(container=Container()))
    assert [n["text"] for n in client.get("/?ordering=-id").json()] == ["long two", "long one"]
    assert [n["text"] for n in client.get("/?text=long one").json()] == ["long one"]


@pytest.mark.django_db(transaction=True)
async def test_async_mode_from_settings_uses_the_same_classes():
    ada = await User.objects.acreate(username="ada")

    class Notes(CRUDController[Note, NoteOut, NoteIn]):
        owner_field = "owner"

    with override_settings(NINJA_DEVX={"ASYNC_MODE": "async"}):
        router = Notes.as_router()
    operations = [op for view in router.path_operations.values() for op in view.operations]
    assert all(operation.is_async for operation in operations)

    client = TestAsyncClient(router)
    created = await client.post("/", json={"text": "async"}, user=ada)
    assert created.status_code == 201
    pk = created.json()["id"]
    assert (await client.patch(f"/{pk}", json={"text": "patched"}, user=ada)).json()[
        "text"
    ] == "patched"
    assert (await client.delete(f"/{pk}", user=ada)).status_code == 204


@pytest.mark.django_db(transaction=True)
async def test_async_crud_controller_alias_and_denials():
    ada = await User.objects.acreate(username="ada")

    class Notes(AsyncCRUDController[Note, NoteOut, NoteIn]):
        options = ControllerOptions(permissions=[DenyAll()])

    client = TestAsyncClient(Notes.as_router())
    assert (await client.get("/", user=ada)).status_code == 403


@pytest.mark.django_db(transaction=True)
async def test_fetch_mode_raise_blocks_lazy_loads_in_async_operations():
    import django

    if django.VERSION < (6, 1):
        pytest.skip("QuerySet.fetch_mode needs Django 6.1")
    ada = await User.objects.acreate(username="ada")
    article = await Article.objects.acreate(title="A", slug="a", author=ada)

    class Lazy(Schema):
        id: int
        author_name: str

        @staticmethod
        def resolve_author_name(obj: Article) -> str:
            return obj.author.username  # not optimized: a lazy load

    class Articles(AsyncReadOnlyModelController[Article, Lazy]):
        optimize_queries = False

    with override_settings(NINJA_DEVX={"ASYNC_FETCH_MODE": "raise"}):
        router = Articles.as_router()
        with pytest.raises(Exception, match=r"(?i)fetch|synchronous"):
            await TestAsyncClient(router).get(f"/{article.pk}")


def test_openapi_for_new_parameters():
    api = NinjaAPI()

    class Notes(CRUDController[Note, NoteOut, NoteIn]):
        pass

    api.add_router("", Notes.as_router())
    schema = api.get_openapi_schema(path_prefix="")
    assert "NoteInPatch" in schema["components"]["schemas"]
