import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Value
from ninja import NinjaAPI
from ninja.testing import TestClient

from ninja_devx.crud import IContainsSearch, PostgresSearch, ReadOnlyModelController
from ninja_devx.crud.filters import filter_schema_for
from tests.testapp.api import ArticleOut
from tests.testapp.models import Article


class Searchable(ReadOnlyModelController[Article, ArticleOut]):
    search_fields = ("title", "body")
    search_backend = IContainsSearch()


def make_articles() -> None:
    ada = User.objects.create(username="ada")
    Article.objects.create(title="Apple pie", slug="apple", author=ada, body="")
    Article.objects.create(title="Banana", slug="banana", author=ada, body="apple sauce")


@pytest.mark.django_db
def test_controller_uses_the_search_backend():
    make_articles()
    client = TestClient(Searchable.as_router())
    titles = [row["title"] for row in client.get("/?search=apple").json()]
    assert titles == ["Apple pie", "Banana"]


@pytest.mark.django_db
def test_default_icontains_search_still_works():
    make_articles()

    class Plain(ReadOnlyModelController[Article, ArticleOut]):
        search_fields = ("title",)

    client = TestClient(Plain.as_router())
    titles = [row["title"] for row in client.get("/?search=banana").json()]
    assert titles == ["Banana"]


def test_postgres_search_builds_a_query(db):
    if connection.vendor != "postgres":
        pytest.skip("PostgreSQL-only backend")
    queryset = Article.objects.all()
    built = PostgresSearch(config="simple").search(queryset, "apple", ("title", "body"))
    assert "_ndx_search" in str(built.query)


def test_search_parameter_stays_documented_with_a_backend():
    api = NinjaAPI(urls_namespace="search-backend-openapi")
    api.add_router("/articles", Searchable.as_router())
    operation = api.get_openapi_schema(path_prefix="")["paths"]["/articles/"]["get"]
    assert "search" in {parameter["name"] for parameter in operation["parameters"]}


def test_backend_schema_leaves_the_search_term_to_the_backend(db):
    filters = filter_schema_for(Searchable)(search="apple")
    assert "LIKE" not in str(filters.filter(Article.objects.all()).query).upper()


def test_search_backends_without_fields_return_the_queryset(db):
    queryset = Article.objects.all()
    assert IContainsSearch().search(queryset, "x", ()) is queryset
    assert PostgresSearch().search(queryset, "x", ()) is queryset


def test_postgres_search_annotates_and_filters(monkeypatch, db):
    search = pytest.importorskip("django.contrib.postgres.search")  # needs psycopg on 6.0+
    monkeypatch.setattr(search, "SearchVector", lambda *fields, **options: Value(""))
    monkeypatch.setattr(search, "SearchQuery", lambda *args, **options: Value(""))
    built = PostgresSearch(config="simple").search(Article.objects.all(), "x", ("title",))
    assert "_ndx_search" in built.query.annotations
