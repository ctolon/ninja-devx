import django
import pytest
from django.contrib.auth.models import User
from django.db import connection, models
from django.test.utils import isolate_apps
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


@pytest.fixture
def counter_model():
    with isolate_apps("tests.testapp"):

        class Counter(models.Model):
            name = models.CharField(max_length=20)
            hits = models.IntegerField(db_default=7)
            doubled = models.GeneratedField(
                expression=models.F("hits") * 2,
                output_field=models.IntegerField(),
                db_persist=True,
            )

            class Meta:
                app_label = "testapp"

        with connection.schema_editor() as editor:
            editor.create_model(Counter)
        yield Counter
        with connection.schema_editor() as editor:
            editor.delete_model(Counter)


@pytest.mark.skipif(django.VERSION < (5, 0), reason="db_default and GeneratedField")
@pytest.mark.django_db(transaction=True)
def test_database_defaults_are_optional_and_generated_fields_read_only(counter_model):
    class Counters(AutoCRUDController[counter_model]):
        pass

    _, in_ = model_schemas(counter_model)
    assert set(in_.model_fields) == {"name", "hits"}
    assert not in_.model_fields["hits"].is_required()

    client = TestClient(Counters.as_router())
    defaulted = client.post("/", json={"name": "a"})
    assert defaulted.status_code == 201, defaulted.json()
    assert (defaulted.json()["hits"], defaulted.json()["doubled"]) == (7, 14)
    explicit = client.post("/", json={"name": "b", "hits": 2}).json()
    assert (explicit["hits"], explicit["doubled"]) == (2, 4)


@pytest.mark.skipif(django.VERSION < (5, 2), reason="CompositePrimaryKey")
def test_composite_primary_keys_need_a_lookup_field():
    with isolate_apps("tests.testapp"):

        class Line(models.Model):
            pk = models.CompositePrimaryKey("order", "number")
            order = models.IntegerField()
            number = models.IntegerField()
            code = models.CharField(max_length=20, unique=True)

            class Meta:
                app_label = "testapp"

        class Lines(AutoCRUDController[Line]):
            pass

        class LinesByCode(AutoCRUDController[Line]):
            lookup_field = "code"
            lookup_param = "code"

        with pytest.raises(ControllerConfigError, match="composite primary key"):
            Lines.as_router()
        LinesByCode.as_router()
