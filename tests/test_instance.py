import pytest
from django.contrib.auth.models import User
from django.http import HttpRequest
from ninja import NinjaAPI, Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Controller, ControllerConfigError, ControllerOptions, IsOwner, get, post
from ninja_devx.crud import Instance, RetrieveMixin
from tests.testapp.api import ArticleOut
from tests.testapp.models import Article, Tag

pytestmark = pytest.mark.django_db


class TagOut(Schema):
    id: int
    name: str


class ArticleActions(RetrieveMixin[Article, ArticleOut]):
    options = ControllerOptions(permissions=[IsOwner("author")])

    @post("/{pk}/publish", response=ArticleOut)
    def publish(self, request: HttpRequest, article: Instance[Article]) -> Article:
        article.published = True
        article.save(update_fields=["published"])
        return article

    @get("/{pk}/async-title", response=dict[str, str])
    async def async_title(self, request: HttpRequest, article: Instance[Article]) -> dict[str, str]:
        return {"title": article.title}


class TagController(Controller):
    @get("/{pk}", response=TagOut)
    def retrieve(self, request: HttpRequest, tag: Instance[Tag]) -> Tag:
        return tag


@pytest.fixture
def article():
    author = User.objects.create(username="ada")
    return Article.objects.create(title="Hello", slug="hello", author=author)


def test_instance_is_loaded_from_the_path(article):
    client = TestClient(ArticleActions.as_router())
    response = client.post(f"/{article.pk}/publish", user=article.author)
    assert response.json()["published"] is True
    assert client.post("/999/publish", user=article.author).status_code == 404


def test_instance_applies_object_permissions(article):
    other = User.objects.create(username="bob")
    assert (
        TestClient(ArticleActions.as_router())
        .post(f"/{article.pk}/publish", user=other)
        .status_code
        == 403
    )


@pytest.mark.django_db(transaction=True)
async def test_instance_in_async_operations():
    author = await User.objects.acreate(username="ada")
    article = await Article.objects.acreate(title="Async", slug="async", author=author)
    client = TestAsyncClient(ArticleActions.as_router())
    response = await client.get(f"/{article.pk}/async-title", user=author)
    assert response.json() == {"title": "Async"}


def test_instance_of_another_model_on_a_plain_controller():
    tag = Tag.objects.create(name="python")
    client = TestClient(TagController.as_router())
    assert client.get(f"/{tag.pk}").json() == {"id": tag.pk, "name": "python"}
    assert client.get("/999").status_code == 404


def test_instance_in_openapi():
    api = NinjaAPI()
    api.add_router("/articles", ArticleActions.as_router())
    operation = api.get_openapi_schema(path_prefix="")["paths"]["/articles/{pk}/publish"]["post"]
    assert [(p["name"], p["in"], p["schema"]["type"]) for p in operation["parameters"]] == [
        ("pk", "path", "integer")
    ]
    assert 404 in operation["responses"]


def test_instance_needs_a_pk_segment():
    class Broken(Controller):
        @get("/latest")
        def latest(self, request: HttpRequest, tag: Instance[Tag]) -> None: ...

    with pytest.raises(ControllerConfigError, match=r"needs a \{pk\} segment"):
        Broken.as_router()


def test_instance_needs_a_model():
    class NotAModel(Controller):
        @get("/{pk}")
        def view(self, request: HttpRequest, value: Instance[str]) -> None: ...  # type: ignore[type-var]

    with pytest.raises(ControllerConfigError, match="needs a model"):
        NotAModel.as_router()
