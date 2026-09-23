import pytest
from django.contrib.auth.models import User
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx.crud import AsyncReadOnlyModelController, ReadOnlyModelController
from ninja_devx.crud.pagination import (
    LimitOffsetPagination,
    _reltuples_estimate,
    _total,
    _unfiltered,
)
from ninja_devx.http.middleware import use_middleware
from ninja_devx.http.pagination_headers import PaginationHeadersMiddleware
from ninja_devx.testing.clients import assert_max_queries
from tests.testapp.models import Article

pytestmark = pytest.mark.django_db(transaction=True)


class ArticleOut(Schema):
    id: int
    title: str


class Exact(ReadOnlyModelController[Article, ArticleOut]):
    pagination_class = LimitOffsetPagination


class NoCount(ReadOnlyModelController[Article, ArticleOut]):
    pagination_class = LimitOffsetPagination
    pagination_options = {"count": False}


class Capped(ReadOnlyModelController[Article, ArticleOut]):
    pagination_class = LimitOffsetPagination
    pagination_options = {"count": 3}


class AsyncCapped(AsyncReadOnlyModelController[Article, ArticleOut]):
    pagination_class = LimitOffsetPagination
    pagination_options = {"count": 3}


class Estimated(ReadOnlyModelController[Article, ArticleOut]):
    pagination_class = LimitOffsetPagination
    pagination_options = {"count": "estimate"}


def _router(controller):
    router = controller.as_router()
    use_middleware(router, PaginationHeadersMiddleware())
    return router


@pytest.fixture
def ada(db):
    return User.objects.create(username="ada")


def make_articles(ada, n):
    for i in range(n):
        Article.objects.create(title=f"a{i}", slug=f"s{i}", author=ada)


def test_count_true_is_exact_and_the_json_shape_is_unchanged(ada):
    make_articles(ada, 3)
    client = TestClient(_router(Exact))
    with assert_max_queries(2):  # list + COUNT(*)
        response = client.get("/?limit=2")
    data = response.json()
    assert data["count"] == 3
    assert response.headers["X-Total-Count"] == "3"


def test_count_false_skips_the_count_query(ada):
    make_articles(ada, 3)
    client = TestClient(_router(NoCount))
    with assert_max_queries(1):  # list only, one extra row fetched for "next"
        response = client.get("/?limit=2")
    assert response.json()["count"] is None
    assert "X-Total-Count" not in response.headers


def test_integer_threshold_is_exact_under_the_limit(ada):
    make_articles(ada, 2)
    client = TestClient(_router(Capped))
    response = client.get("/?limit=2")
    assert response.json()["count"] == 2
    assert response.headers["X-Total-Count"] == "2"


def test_integer_threshold_caps_and_reports_a_lower_bound(ada):
    make_articles(ada, 5)
    client = TestClient(_router(Capped))
    with assert_max_queries(2):  # list + a capped COUNT(*), never N+1 full rows
        response = client.get("/?limit=2")
    data = response.json()
    assert data["count"] == 3
    assert isinstance(data["count"], int)
    assert response.headers["X-Total-Count"] == "3+"


async def test_integer_threshold_works_for_async_lists():
    ada = await User.objects.acreate(username="ada")
    for i in range(5):
        await Article.objects.acreate(title=f"a{i}", slug=f"s{i}", author=ada)
    client = TestAsyncClient(AsyncCapped.as_router())
    response = await client.get("/?limit=2")
    assert response.json()["count"] == 3


def test_estimate_falls_back_to_exact_on_non_postgres_backends(ada):
    make_articles(ada, 4)
    client = TestClient(_router(Estimated))
    response = client.get("/")
    assert response.json()["count"] == 4
    assert response.headers["X-Total-Count"] == "4"


def test_unfiltered_detects_a_bare_queryset(ada):
    make_articles(ada, 1)
    assert _unfiltered(Article.objects.all()) is True
    assert _unfiltered(Article.objects.filter(published=True)) is False


def test_reltuples_estimate_is_none_on_sqlite(ada):
    make_articles(ada, 1)
    assert _reltuples_estimate(Article.objects.all()) is None


def test_total_uses_the_estimate_only_for_unfiltered_querysets(ada, monkeypatch):
    make_articles(ada, 3)
    monkeypatch.setattr("ninja_devx.crud.pagination._reltuples_estimate", lambda queryset: 42)
    assert _total("estimate", Article.objects.all()) == (42, False)
    filtered = Article.objects.filter(published=True)
    assert _total("estimate", filtered) == (filtered.count(), False)
