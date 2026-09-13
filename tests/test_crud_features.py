import types
from collections.abc import Mapping
from typing import ClassVar

import pytest
from django.contrib.auth.models import Permission, User
from django.http import HttpRequest
from django.test import override_settings
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient
from pydantic import BaseModel

from ninja_devx import Container, ControllerConfigError, ControllerOptions, DjangoModelPermissions
from ninja_devx.crud import (
    BulkCreateMixin,
    BulkDestroyMixin,
    BulkUpdateMixin,
    CRUDController,
    ModelRepository,
    ModelService,
    Parent,
    ReadOnlyModelController,
    SoftDeleteMixin,
    get_parent,
    related_lookups,
)
from ninja_devx.layers import Conflict, dual
from ninja_devx.testing.clients import assert_max_queries
from tests.testapp.models import Article, Comment, Note, Tag

pytestmark = pytest.mark.django_db


# --- Schemas -------------------------------------------------------------------


class NoteOut(Schema):
    id: int
    text: str
    status: str
    priority: int
    archived: bool
    owner_id: int


class NoteIn(Schema):
    text: str
    status: str = "draft"
    priority: int = 0


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


class CommentOut(Schema):
    id: int
    body: str
    article_id: int


class CommentIn(Schema):
    body: str


@pytest.fixture
def ada():
    return User.objects.create(username="ada")


@pytest.fixture
def bob():
    return User.objects.create(username="bob")


# --- Ownership -------------------------------------------------------------------


class NoteController(CRUDController[Note, NoteOut, NoteIn]):
    owner_field = "owner"
    search_fields = ("text",)
    filter_fields = {"status": ("exact",), "priority": ("gte", "lte", "in"), "archived": ("exact",)}
    ordering_fields = ("priority",)


class ScopedNoteController(NoteController):
    scope_queryset_to_owner = True


def test_owner_is_set_from_the_request_user(ada):
    response = TestClient(NoteController.as_router()).post("/", json={"text": "hi"}, user=ada)
    assert response.status_code == 201
    assert response.json()["owner_id"] == ada.pk


def test_ownership_requires_authentication_and_protects_objects(ada, bob):
    note = Note.objects.create(owner=ada, text="mine")
    client = TestClient(NoteController.as_router())
    assert client.get("/").status_code == 401
    assert client.get(f"/{note.pk}", user=bob).status_code == 403
    assert client.get(f"/{note.pk}", user=ada).status_code == 200
    assert len(client.get("/", user=bob).json()) == 1  # lists are not scoped by default


def test_scope_queryset_to_owner(ada, bob):
    Note.objects.create(owner=ada, text="mine")
    note = Note.objects.create(owner=bob, text="theirs")
    client = TestClient(ScopedNoteController.as_router())
    assert [item["text"] for item in client.get("/", user=ada).json()] == ["mine"]
    assert client.get(f"/{note.pk}", user=ada).status_code == 404


def test_input_schema_must_not_accept_the_owner():
    class Leaky(BaseModel):
        text: str
        owner: int

    class LeakyController(CRUDController[Note, NoteOut, Leaky]):
        owner_field = "owner"

    with pytest.raises(ControllerConfigError, match=r"must not accept \['owner'\]"):
        LeakyController.as_router()


# --- Generated filters ------------------------------------------------------------


def test_generated_filters(ada):
    Note.objects.create(owner=ada, text="buy milk", priority=1)
    Note.objects.create(owner=ada, text="call mom", status="done", priority=5)
    Note.objects.create(owner=ada, text="buy bread", priority=9, archived=True)
    client = TestClient(NoteController.as_router())

    def texts(query: str) -> list[str]:
        return [item["text"] for item in client.get(f"/?{query}", user=ada).json()]

    assert texts("search=buy") == ["buy milk", "buy bread"]
    assert texts("status=done") == ["call mom"]
    assert texts("priority__gte=2&priority__lte=6") == ["call mom"]
    assert texts("priority__in=1&priority__in=9") == ["buy milk", "buy bread"]
    assert texts("archived=true") == ["buy bread"]
    assert texts("ordering=-priority") == ["buy bread", "call mom", "buy milk"]
    assert client.get("/?status=unknown", user=ada).status_code == 422


def test_generated_filters_in_openapi():
    api = NinjaAPI()
    api.add_router("", NoteController.as_router())
    parameters = {
        p["name"]: p["schema"]
        for p in api.get_openapi_schema(path_prefix="")["paths"]["/"]["get"]["parameters"]
    }
    assert set(parameters) >= {
        "search",
        "status",
        "priority__gte",
        "priority__lte",
        "priority__in",
        "archived",
    }
    status = parameters["status"]
    assert {"draft", "done"} <= set(status["anyOf"][0]["enum"])


def make_controller(name, attributes):
    base = CRUDController[Note, NoteOut, NoteIn]
    return types.new_class(name, (base,), exec_body=lambda namespace: namespace.update(attributes))


@pytest.mark.parametrize(
    ("attributes", "message"),
    [
        ({"filter_fields": {"nope": ("exact",)}}, "no field 'nope'"),
        ({"filter_fields": {"text": ("regex",)}}, "unsupported lookup 'regex'"),
        ({"search_fields": ("missing",)}, "no field 'missing'"),
    ],
)
def test_filter_configuration_errors(attributes, message):
    controller = make_controller("BadFilters", attributes)
    with pytest.raises(ControllerConfigError, match=message):
        controller.as_router()


def test_filter_schema_and_generated_filters_are_exclusive():
    from ninja import FilterSchema

    class Explicit(FilterSchema):
        text: str | None = None

    controller = make_controller("Both", {"filter_schema": Explicit, "search_fields": ("text",)})
    with pytest.raises(ControllerConfigError, match="either filter_schema"):
        controller.as_router()


# --- Django model permissions -----------------------------------------------------------


def test_django_model_permissions(ada, bob):
    class Guarded(CRUDController[Tag, TagOut, TagOut]):
        options = ControllerOptions(permissions=[DjangoModelPermissions()])

        def perform_create(self, request: HttpRequest, payload: BaseModel) -> Tag:
            return Tag.objects.create(name=payload.model_dump()["name"])

    ada.user_permissions.add(*Permission.objects.filter(codename__in=["view_tag", "add_tag"]))
    client = TestClient(Guarded.as_router())
    assert client.get("/", user=ada).status_code == 200
    assert client.post("/", json={"id": 0, "name": "x"}, user=ada).status_code == 201
    assert client.get("/", user=bob).status_code == 403
    tag = Tag.objects.get()
    assert client.delete(f"/{tag.pk}", user=ada).status_code == 403


# --- N+1 ----------------------------------------------------------------------------


class ArticleReadController(ReadOnlyModelController[Article, ArticleDetail]):
    pass


def test_related_lookups_follow_the_output_schema():
    assert related_lookups(Article, ArticleDetail) == (("author",), ("tags",))


def test_list_runs_a_constant_number_of_queries(ada):
    python, django = Tag.objects.create(name="python"), Tag.objects.create(name="django")
    for index in range(10):
        article = Article.objects.create(title=f"A{index}", slug=f"a{index}", author=ada)
        article.tags.set([python, django])

    client = TestClient(ArticleReadController.as_router())
    with assert_max_queries(2):  # articles + authors (joined), tags (prefetched)
        response = client.get("/")
    assert len(response.json()) == 10
    assert response.json()[0]["author"] == {"id": ada.pk, "username": "ada"}


def test_query_optimization_can_be_disabled(ada):
    class Unoptimized(ArticleReadController):
        optimize_queries = False

    Article.objects.create(title="A", slug="a", author=ada)
    with pytest.raises(AssertionError, match="Expected at most 1"), assert_max_queries(1):
        TestClient(Unoptimized.as_router()).get("/")


# --- Nested resources -----------------------------------------------------------------


class CommentController(CRUDController[Comment, CommentOut, CommentIn]):
    parent = Parent(Article, field="article")


def test_nested_resources(ada):
    first = Article.objects.create(title="A", slug="a", author=ada)
    second = Article.objects.create(title="B", slug="b", author=ada)
    Comment.objects.create(article=second, body="elsewhere")

    api = NinjaAPI(urls_namespace="nested")
    api.add_router("/articles/{article_pk}/comments", CommentController.as_router())
    client = TestClient(api)

    created = client.post(f"/articles/{first.pk}/comments/", json={"body": "hello"})
    assert (created.status_code, created.json()["article_id"]) == (201, first.pk)
    assert [c["body"] for c in client.get(f"/articles/{first.pk}/comments/").json()] == ["hello"]
    other = Comment.objects.get(body="elsewhere")
    assert client.get(f"/articles/{first.pk}/comments/{other.pk}").status_code == 404
    assert client.get("/articles/999/comments/").status_code == 404

    operation = api.get_openapi_schema(path_prefix="")["paths"]["/articles/{article_pk}/comments/"][
        "get"
    ]
    assert operation["parameters"][0]["name"] == "article_pk"


def test_parent_must_match_the_model():
    class Wrong(CRUDController[Comment, CommentOut, CommentIn]):
        parent = Parent(Tag, field="article")

    with pytest.raises(ControllerConfigError, match="does not point to Tag"):
        Wrong.as_router()


def test_get_parent_outside_nested_operations():
    with pytest.raises(RuntimeError, match="outside a nested operation"):
        get_parent(HttpRequest())


# --- Soft delete ----------------------------------------------------------------------


class SoftNoteController(SoftDeleteMixin[Note, NoteOut], CRUDController[Note, NoteOut, NoteIn]):
    pass


class ArchivedNoteController(SoftDeleteMixin[Note, NoteOut], CRUDController[Note, NoteOut, NoteIn]):
    soft_delete = "archived"


@pytest.mark.parametrize("controller", [SoftNoteController, ArchivedNoteController])
def test_soft_delete_and_restore(controller, ada):
    note = Note.objects.create(owner=ada, text="keep me")
    client = TestClient(controller.as_router())

    assert client.delete(f"/{note.pk}").status_code == 204
    assert Note.objects.filter(pk=note.pk).exists()
    assert client.get(f"/{note.pk}").status_code == 404
    assert client.get("/").json() == []

    restored = client.post(f"/{note.pk}/restore")
    assert (restored.status_code, restored.json()["id"]) == (200, note.pk)
    assert client.get(f"/{note.pk}").status_code == 200


def test_soft_delete_values_must_be_explicit_for_other_fields():
    class Wrong(SoftDeleteMixin[Note, NoteOut], CRUDController[Note, NoteOut, NoteIn]):
        soft_delete = "text"

    with pytest.raises(ControllerConfigError, match="pass deleted"):
        Wrong.as_router()


# --- Bulk -----------------------------------------------------------------------------


class TagIn(Schema):
    name: str


class BulkTags(
    BulkCreateMixin[Tag, TagOut, TagIn],
    BulkUpdateMixin[Tag, TagOut, TagIn],
    BulkDestroyMixin[Tag],
    CRUDController[Tag, TagOut, TagIn],
):
    bulk_limit = 3


def test_bulk_create_update_and_delete():
    client = TestClient(BulkTags.as_router())
    created = client.post("/bulk", json=[{"name": "a"}, {"name": "b"}])
    assert created.status_code == 201
    pks = [item["id"] for item in created.json()]
    assert [item["name"] for item in created.json()] == ["a", "b"]

    updated = client.post("/bulk-update", json={"pks": pks, "data": {"name": "same"}})
    assert updated.status_code == 422  # names are unique: model validation still runs per object
    assert set(Tag.objects.values_list("name", flat=True)) == {"a", "b"}  # rolled back

    single = client.post("/bulk-update", json={"pks": pks[:1], "data": {"name": "renamed"}})
    assert single.json() == [{"id": pks[0], "name": "renamed"}]

    assert client.post("/bulk-delete", json={"pks": [*pks, 999]}).status_code == 404
    assert client.post("/bulk-delete", json={"pks": pks}).status_code == 204
    assert not Tag.objects.exists()


def test_bulk_limits():
    client = TestClient(BulkTags.as_router())
    too_many = [{"name": str(index)} for index in range(4)]
    assert client.post("/bulk", json=too_many).status_code == 422
    assert client.post("/bulk-delete", json={"pks": [1, 2, 3, 4]}).status_code == 422
    assert not Tag.objects.exists()


def test_static_routes_match_before_lookups():
    router = BulkTags.as_router()
    paths = list(router.path_operations)
    assert paths.index("/bulk") < paths.index("/{pk}")


def test_lookup_converter_is_opt_in():
    class Converted(BulkTags):
        lookup_converter = True

    assert "/{int:pk}" in Converted.as_router().path_operations


# --- Service layer ----------------------------------------------------------------------


class AuditedRepository(ModelRepository[Note]):
    saved: ClassVar[list[str]] = []

    def add(self, data: Mapping[str, object], /) -> Note:
        AuditedRepository.saved.append(str(data["text"]))
        return super().add(data)


class NoteWriteService(ModelService[Note]):
    def __init__(self, repository: AuditedRepository) -> None:
        super().__init__(repository)

    @dual
    def create(self, data: Mapping[str, object]) -> Note:
        if "forbidden" in str(data["text"]):
            raise Conflict("Notes cannot say that")
        return super().create(data)


class ServicedNotes(CRUDController[Note, NoteOut, NoteIn]):
    owner_field = "owner"
    service_class = NoteWriteService


def test_writes_go_through_the_default_service(ada):
    response = TestClient(NoteController.as_router()).post("/", json={"text": "plain"}, user=ada)
    assert response.status_code == 201


def test_service_class_is_resolved_from_the_container(ada):
    AuditedRepository.saved.clear()
    client = TestClient(ServicedNotes.as_router(container=Container()))
    created = client.post("/", json={"text": "audited"}, user=ada)
    assert (created.status_code, created.json()["owner_id"]) == (201, ada.pk)
    assert AuditedRepository.saved == ["audited"]

    conflict = client.post("/", json={"text": "forbidden words"}, user=ada)
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Notes cannot say that", "code": "conflict"}


def test_service_class_without_container_is_constructed(ada):
    class Plain(CRUDController[Note, NoteOut, NoteIn]):
        owner_field = "owner"
        service_class = ModelService

    with pytest.raises(TypeError, match="needs a model"):
        TestClient(Plain.as_router()).post("/", json={"text": "x"}, user=ada)


# --- Settings ---------------------------------------------------------------------------


def test_settings_provide_defaults_that_class_attributes_override(ada):
    Note.objects.bulk_create([Note(owner=ada, text=str(i)) for i in range(3)])
    settings = {
        "PAGINATION_CLASS": "ninja.pagination.LimitOffsetPagination",
        "DEFAULT_OPTIONS": ControllerOptions(tags=["from-settings"]),
    }
    with override_settings(NINJA_DEVX=settings):
        page = TestClient(NoteController.as_router()).get("/?limit=2", user=ada).json()
        assert (page["count"], len(page["items"])) == (3, 2)

        class Unpaginated(NoteController):
            pagination_class = None

        assert len(TestClient(Unpaginated.as_router()).get("/", user=ada).json()) == 3
        assert NoteController.merged_options()["tags"] == ["from-settings"]


def test_unknown_settings_are_rejected():
    from django.core.exceptions import ImproperlyConfigured

    with (
        override_settings(NINJA_DEVX={"PAGINATE": True}),
        pytest.raises(ImproperlyConfigured, match=r"Unknown settings\.NINJA_DEVX keys"),
    ):
        NoteController.as_router()
