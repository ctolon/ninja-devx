from uuid import UUID, uuid4

import pytest
from django.contrib.auth.models import User
from django.db import models
from django.db.models import QuerySet
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient
from pydantic import BaseModel

from ninja_devx import ControllerConfigError, ControllerOptions, Scope
from ninja_devx.crud import (
    CRUDController,
    ListMixin,
    ModelController,
    ReadOnlyModelController,
    RetrieveMixin,
    save_instance,
)
from tests.testapp.api import ArticleController, ArticleIn, ArticleOut, SlugArticleController
from tests.testapp.models import Article, Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def ada():
    return User.objects.create(username="ada")


@pytest.fixture
def client(ada):
    return TestClient(ArticleController.as_router(), headers={"X-User": "ada"})


def make_article(author, **fields):
    defaults = {"title": "Title", "slug": f"slug-{uuid4().hex[:8]}"}
    return Article.objects.create(author=author, **{**defaults, **fields})


# --- Endpoints ---------------------------------------------------------------


def test_create_sets_fields_many_to_many_and_owner(client, ada):
    tag = Tag.objects.create(name="python")
    response = client.post("/", json={"title": "Hello", "slug": "hello", "tags": [tag.pk]})

    assert response.status_code == 201
    body = response.json()
    assert (body["title"], body["author_id"], body["tags"]) == ("Hello", ada.pk, [tag.pk])
    assert Article.objects.get(slug="hello").tags.get() == tag


def test_model_validation_errors_become_422(client, ada):
    make_article(ada, slug="taken")
    response = client.post("/", json={"title": "Dup", "slug": "taken"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "slug"]


def test_schema_validation_errors_are_ninjas(client):
    assert client.post("/", json={"slug": "no-title"}).status_code == 422


def test_list_paginates_filters_and_orders(client, ada):
    make_article(ada, title="b python", published=True)
    make_article(ada, title="a django")
    make_article(ada, title="c python")

    page = client.get("/?search=python&ordering=title").json()
    assert page["count"] == 2
    assert [item["title"] for item in page["items"]] == ["b python", "c python"]

    published = client.get("/?published=true").json()
    assert [item["title"] for item in published["items"]] == ["b python"]

    default = client.get("/").json()  # default_ordering = ("-created",)
    assert [item["title"] for item in default["items"]] == ["c python", "a django", "b python"]


def test_ordering_is_restricted_to_ordering_fields(client):
    assert client.get("/?ordering=body").status_code == 422


def test_retrieve_and_404(client, ada):
    article = make_article(ada)
    assert client.get(f"/{article.pk}").json()["id"] == article.pk
    assert client.get("/999").status_code == 404
    invalid = client.get("/not-a-number")  # reaches Ninja: a JSON 422, not Django's HTML 404
    assert invalid.status_code == 422


def test_put_replaces_and_patch_updates_only_sent_fields(client, ada):
    article = make_article(ada, body="original")

    put = client.put(f"/{article.pk}", json={"title": "New", "slug": article.slug})
    assert (put.status_code, put.json()["body"]) == (200, "")

    client.patch(f"/{article.pk}", json={"body": "patched"})
    article.refresh_from_db()
    assert (article.title, article.body) == ("New", "patched")


def test_destroy(client, ada):
    article = make_article(ada)
    assert client.delete(f"/{article.pk}").status_code == 204
    assert not Article.objects.filter(pk=article.pk).exists()


def test_object_permissions_apply_to_every_detail_route(client, ada):
    bob = User.objects.create(username="bob")
    article = make_article(bob)
    for method in ("get", "put", "patch", "delete"):
        response = getattr(client, method)(f"/{article.pk}", json={"title": "x", "slug": "x"})
        assert response.status_code == 403, method
    assert client.get(f"/{article.pk}/summary").status_code == 403


def test_authentication_is_required(ada):
    client = TestClient(ArticleController.as_router())
    assert client.get("/").status_code == 401


def test_literal_routes_are_not_shadowed_by_the_lookup(ada):
    from ninja_devx import get

    class WithMe(ArticleController):
        @get("/me", response=dict[str, str])
        def me(self, request: HttpRequest) -> dict[str, str]:
            return {"user": str(getattr(request, "auth", ""))}

    client = TestClient(WithMe.as_router(), headers={"X-User": "ada"})
    assert client.get("/me").json() == {"user": "ada"}


def test_custom_operations_reuse_get_object(client, ada):
    article = make_article(ada, title="Summary")
    assert client.get(f"/{article.pk}/summary").json() == {"title": "Summary"}


def test_lookup_field_and_singleton_scope(ada):
    make_article(ada, slug="hello-world", title="By slug")
    client = TestClient(SlugArticleController.as_router(), headers={"X-User": "ada"})
    assert client.get("/hello-world").json()["title"] == "By slug"
    assert client.get("/missing").status_code == 404


# --- OpenAPI -----------------------------------------------------------------


def test_generic_arguments_reach_openapi():
    api = NinjaAPI()
    api.add_router("/articles", ArticleController.as_router())
    schema = api.get_openapi_schema(path_prefix="")
    paths = schema["paths"]

    detail = paths["/articles/{pk}"]
    assert detail["get"]["parameters"][0]["schema"]["type"] == "integer"
    body_ref = detail["put"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert body_ref.endswith("/ArticleIn")
    patch_schema = detail["patch"]["requestBody"]["content"]["application/json"]["schema"]
    assert patch_schema["$ref"].endswith("/ArticleInPatch")
    list_params = {p["name"] for p in paths["/articles/"]["get"]["parameters"]}
    assert list_params == {"search", "published", "ordering", "page", "page_size"}
    ordering = next(p for p in paths["/articles/"]["get"]["parameters"] if p["name"] == "ordering")
    assert ordering["schema"]["items"]["enum"] == ["created", "-created", "title", "-title"]


# --- Composition and configuration ------------------------------------------


class ArticleSummary(Schema):
    id: int
    title: str


class SummaryController(ListMixin[Article, ArticleSummary], RetrieveMixin[Article, ArticleSummary]):
    pass


def test_mixins_compose_with_their_own_generic_arguments(ada):
    article = make_article(ada, title="Composed")
    router = SummaryController.as_router()
    assert list(router.path_operations) == ["/", "/{pk}"]
    client = TestClient(router)
    assert client.get("/").json() == [{"id": article.pk, "title": "Composed"}]
    assert client.get(f"/{article.pk}").json() == {"id": article.pk, "title": "Composed"}


def test_read_only_controller(ada):
    class ReadOnlyArticles(ReadOnlyModelController[Article, ArticleSummary]):
        pass

    router = ReadOnlyArticles.as_router()
    methods = {path: view.operations[0].methods for path, view in router.path_operations.items()}
    assert methods == {"/": ["GET"], "/{pk}": ["GET"]}


def test_unparameterized_controller_is_rejected():
    class Unbound(ModelController):  # type: ignore[type-arg]
        pass

    with pytest.raises(ControllerConfigError, match="parameterize its model"):
        Unbound.get_model()

    with pytest.raises(ControllerConfigError, match="parameterize its model"):
        RetrieveMixin.as_router()


def test_unbound_type_variables_in_operations_are_rejected():
    from typing import Generic, TypeVar

    from ninja_devx import Controller, get

    T = TypeVar("T")

    class GenericController(Controller, Generic[T]):
        @get("/")
        def index(self, request, value: T): ...

    with pytest.raises(ControllerConfigError, match="unbound type variables"):
        GenericController.as_router()

    class Bound(GenericController[int]):
        pass

    assert TestClient(Bound.as_router()).get("/?value=3").status_code == 200


def test_conflicting_generic_arguments_are_rejected():
    class Other(Schema):
        id: int

    class Conflicting(ListMixin[Article, ArticleSummary], RetrieveMixin[Article, Other]):
        pass

    with pytest.raises(ControllerConfigError, match="conflicting arguments"):
        Conflicting.as_router()


def test_input_schema_must_match_the_model():
    class Wrong(BaseModel):
        title: str
        nonsense: int

    class WrongController(CRUDController[Article, ArticleOut, Wrong]):
        pass

    with pytest.raises(ControllerConfigError, match=r"\['nonsense'\] do not exist on Article"):
        WrongController.as_router()


def test_overridden_perform_hooks_skip_the_schema_check():
    class Wrong(BaseModel):
        title: str
        slug: str
        author_name: str

    class MappedController(CRUDController[Article, ArticleOut, Wrong]):
        options = ControllerOptions(permissions=[])

        def perform_create(self, request: HttpRequest, payload: Wrong) -> Article:
            author = User.objects.get(username=payload.author_name)
            return save_instance(
                Article(author=author), payload.model_dump(exclude={"author_name"})
            )

        def perform_update(self, request, instance, data):
            return save_instance(instance, {k: v for k, v in data.items() if k != "author_name"})

    User.objects.create(username="ada")
    client = TestClient(MappedController.as_router())
    response = client.post("/", json={"title": "T", "slug": "t", "author_name": "ada"})
    assert response.status_code == 201


def test_uuid_and_char_lookups():
    class Thing(models.Model):
        uuid = models.UUIDField(unique=True, default=uuid4)
        code = models.CharField(max_length=10, unique=True)

        class Meta:
            app_label = "testapp"

    class ThingOut(Schema):
        code: str

    class ByUuid(RetrieveMixin[Thing, ThingOut]):
        lookup_field = "uuid"
        scope = Scope.SINGLETON

    class ByCode(RetrieveMixin[Thing, ThingOut]):
        lookup_field = "code"

    api = NinjaAPI()
    api.add_router("/uuid", ByUuid.as_router())
    api.add_router("/code", ByCode.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]
    uuid_param = paths["/uuid/{pk}"]["get"]["parameters"][0]["schema"]
    assert (uuid_param["type"], uuid_param["format"]) == ("string", "uuid")
    assert paths["/code/{pk}"]["get"]["parameters"][0]["schema"]["type"] == "string"
    assert UUID  # imported for the annotation above


@pytest.mark.parametrize("reload_result", [True, False])
def test_writes_roll_back_when_get_queryset_excludes_the_result(ada, reload_result):
    class PublishedOnly(CRUDController[Article, ArticleOut, ArticleIn]):
        refresh_after_write = reload_result

        def get_queryset(self, request: HttpRequest) -> QuerySet[Article]:
            return Article.objects.filter(published=True)

        def perform_create(self, request: HttpRequest, payload: ArticleIn) -> Article:
            return save_instance(Article(author=ada), payload.model_dump())

    response = TestClient(PublishedOnly.as_router()).post("/", json={"title": "T", "slug": "t"})
    assert response.status_code == 404
    assert not Article.objects.filter(slug="t").exists()


def test_save_instance_rejects_unknown_fields(ada):
    with pytest.raises(ValueError, match="no field 'nope'"):
        save_instance(Article(author=ada), {"nope": 1})


def test_save_instance_accepts_foreign_key_ids_and_instances(ada):
    by_id = save_instance(Article(title="a", slug="a"), {"author": ada.pk})
    by_instance = save_instance(Article(title="b", slug="b"), {"author": ada})
    assert by_id.author == by_instance.author == ada


def test_refresh_after_write_can_be_disabled(ada):
    class NoRefresh(CRUDController[Article, ArticleOut, ArticleIn]):
        refresh_after_write = False

        def perform_create(self, request: HttpRequest, payload: ArticleIn) -> Article:
            return save_instance(Article(author=ada), payload.model_dump())

    response = TestClient(NoRefresh.as_router()).post("/", json={"title": "T", "slug": "t"})
    assert response.status_code == 201
    assert ArticleIn  # used by the generic arguments above
