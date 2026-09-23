from typing import Annotated

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from ninja import NinjaAPI, Schema
from ninja.testing import TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.crud import ReadOnlyModelController
from ninja_devx.serialization.visibility import (
    Expandable,
    FieldVisibility,
    ResponseShape,
    response_shape,
    set_response_shape,
)
from tests.testapp.models import Article

pytestmark = pytest.mark.django_db


class AuthorOut(Schema):
    id: int
    username: str


class ArticleOut(FieldVisibility, Schema):
    id: int
    title: str
    body: str
    author: Annotated[int | AuthorOut, Expandable()]


class Articles(ReadOnlyModelController[Article, ArticleOut]):
    sparse_fields = True


class Renamed(ReadOnlyModelController[Article, ArticleOut]):
    sparse_fields = True
    fields_param = "select"
    expand_param = "include"


@pytest.fixture
def articles():
    ada = User.objects.create(username="ada")
    return [
        Article.objects.create(title=f"t{i}", slug=f"s{i}", body="b", author=ada) for i in range(3)
    ]


def test_relations_are_keys_by_default_and_need_no_join(articles):
    client = TestClient(Articles.as_router())
    with CaptureQueriesContext(connection) as queries:
        data = client.get("/").json()
    assert data[0] == {
        "id": articles[0].pk,
        "title": "t0",
        "body": "b",
        "author": articles[0].author_id,
    }
    assert len(queries) == 1
    assert "auth_user" not in queries[0]["sql"]


def test_expand_embeds_the_relation_in_one_query(articles):
    client = TestClient(Articles.as_router())
    with CaptureQueriesContext(connection) as queries:
        data = client.get("/?expand=author").json()
    assert data[0]["author"] == {"id": articles[0].author_id, "username": "ada"}
    assert len(queries) == 1


def test_sparse_fields(articles):
    client = TestClient(Articles.as_router())
    item = client.get(f"/{articles[1].pk}?fields=id,title").json()
    assert item == {"id": articles[1].pk, "title": "t1"}
    both = client.get("/?fields=id,author&expand=author").json()
    assert both[0] == {
        "id": articles[0].pk,
        "author": {"id": articles[0].author_id, "username": "ada"},
    }


def test_unknown_names_are_422(articles):
    client = TestClient(Articles.as_router())
    response = client.get("/?fields=id,titel")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "fields"]
    assert client.get("/?expand=tags").status_code == 422


def test_openapi_and_renamed_parameters(articles):
    api = NinjaAPI(urls_namespace="shaping")
    api.add_router("/a", Articles.as_router())
    api.add_router("/b", Renamed.as_router())
    paths = api.get_openapi_schema(path_prefix="")["paths"]
    names = {p["name"] for p in paths["/a/"]["get"]["parameters"]}
    assert {"fields", "expand"} <= names
    assert {"select", "include"} <= {p["name"] for p in paths["/b/{pk}"]["get"]["parameters"]}
    client = TestClient(Renamed.as_router())
    assert client.get(f"/{articles[0].pk}?select=title&include=author").json() == {"title": "t0"}


def test_the_mixin_is_required():
    class BareOut(Schema):
        id: int

    class Bare(ReadOnlyModelController[Article, BareOut]):
        sparse_fields = True

    with pytest.raises(ControllerConfigError, match="FieldVisibility"):
        Bare.as_router()


def test_sparse_responses_are_documented_as_partial(tmp_path, articles):
    import importlib.util
    import sys

    from ninja_devx.codegen import generate_python

    api = NinjaAPI(urls_namespace="partial")
    api.add_router("/a", Articles.as_router())
    document = api.get_openapi_schema(path_prefix="")
    components = document["components"]["schemas"]
    retrieve = document["paths"]["/a/{pk}"]["get"]["responses"][200]["content"]
    assert retrieve["application/json"]["schema"]["$ref"].endswith("/ArticleOutPartial")
    assert "required" not in components["ArticleOutPartial"]
    assert set(components["ArticleOutPartial"]["properties"]) == {"id", "title", "body", "author"}

    path = tmp_path / "partial_client.py"
    path.write_text(generate_python(document))
    spec = importlib.util.spec_from_file_location("partial_client", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["partial_client"] = module
    spec.loader.exec_module(module)
    client = TestClient(Articles.as_router())
    sparse = client.get(f"/{articles[0].pk}?fields=id,title").json()
    assert module.ArticleOutPartial.model_validate(sparse).title == "t0"


def test_response_shape_is_read_from_the_request():
    assert response_shape(None) is None
    request = RequestFactory().get("/x")
    assert response_shape(request) is None
    set_response_shape(request, ResponseShape(schema=object, fields=frozenset({"a"})))
    shape = response_shape(request)
    assert shape is not None
    assert shape.fields == frozenset({"a"})
