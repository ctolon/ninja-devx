import pytest
from django.contrib.auth.models import User
from ninja import NinjaAPI
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.crud import (
    AsyncAutoCRUDController,
    AutoCRUDController,
    AutoReadOnlyController,
    model_schemas,
)
from tests.testapp.models import Article, Tag

pytestmark = pytest.mark.django_db


class Articles(AutoCRUDController[Article]):
    schema_exclude = ("body",)
    read_only_fields = ("author",)

    def perform_create(self, request, payload):
        return self.get_service(request).create({**payload.model_dump(), "author": request.user})


class Tags(AsyncAutoCRUDController[Tag]):
    pass


class ReadTags(AutoReadOnlyController[Tag]):
    pass


def test_generated_schemas():
    out, in_ = model_schemas(Article, exclude=["body"], read_only=["author"])
    assert out.__name__ == "ArticleOut"
    assert set(out.model_fields) == {
        "id",
        "title",
        "slug",
        "author",
        "published",
        "created",
        "tags",
    }
    assert set(in_.model_fields) == {"title", "slug", "published", "tags"}
    assert Articles.output_schema() is out
    assert Articles.input_schema() is in_


def test_crud_round_trip():
    ada = User.objects.create(username="ada")
    tag = Tag.objects.create(name="django")
    client = TestClient(Articles.as_router())
    created = client.post("/", json={"title": "Hi", "slug": "hi", "tags": [tag.pk]}, user=ada)
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["author"] == ada.pk
    assert body["tags"] == [tag.pk]
    assert "body" not in body
    assert (
        client.patch(f"/{body['id']}", json={"title": "Hello"}, user=ada).json()["title"] == "Hello"
    )
    assert client.get("/", user=ada).json()[0]["slug"] == "hi"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_variant():
    client = TestAsyncClient(Tags.as_router())
    created = await client.post("/", json={"name": "ninja"})
    assert created.status_code == 201
    assert (await client.get(f"/{created.json()['id']}")).json()["name"] == "ninja"


def test_openapi_names_and_read_only():
    api = NinjaAPI(urls_namespace="auto")
    api.add_router("/articles", Articles.as_router())
    api.add_router("/tags", ReadTags.as_router())
    schema = api.get_openapi_schema(path_prefix="")
    assert {"ArticleOut", "ArticleIn"} <= set(schema["components"]["schemas"])
    assert set(schema["paths"]["/tags/"]) == {"get"}


def test_misspelled_fields_fail_at_startup():
    class Broken(AutoCRUDController[Article]):
        read_only_fields = ("autor",)

    with pytest.raises(ControllerConfigError, match="Did you mean 'author'"):
        Broken.as_router()
