from typing import Annotated

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx.crud import AsyncReadOnlyModelController, ReadOnlyModelController
from ninja_devx.http.explain import QUERY_PLAN_ATTR, QueryExplainMiddleware
from ninja_devx.http.middleware import use_middleware
from ninja_devx.serialization.visibility import Expandable, FieldVisibility
from tests.testapp.models import Article, Comment

pytestmark = pytest.mark.django_db(transaction=True)


class CommentOut(Schema):
    id: int
    body: str


class ArticleOut(FieldVisibility, Schema):
    id: int
    title: str
    comments: Annotated[list[CommentOut] | None, Expandable()] = None


class Articles(ReadOnlyModelController[Article, ArticleOut]):
    pass


class AsyncArticles(AsyncReadOnlyModelController[Article, ArticleOut]):
    pass


def _router(**middleware_kwargs):
    router = Articles.as_router()
    use_middleware(router, QueryExplainMiddleware(**middleware_kwargs))
    return router


@pytest.fixture
def article():
    ada = User.objects.create(username="ada")
    a = Article.objects.create(title="a1", slug="s1", author=ada)
    Comment.objects.create(article=a, body="hi")
    return a


def test_disabled_by_default_outside_debug(article):
    client = TestClient(_router())
    response = client.get("/")
    assert "X-Query-Count" not in response.headers
    assert "X-Query-Time" not in response.headers
    assert "X-Query-Plan" not in response.headers


def test_follows_debug_setting(article):
    client = TestClient(_router())
    with override_settings(DEBUG=True):
        response = client.get("/")
    assert response.headers["X-Query-Count"] == "1"
    assert float(response.headers["X-Query-Time"]) >= 0


def test_enabled_true_ignores_debug(article):
    client = TestClient(_router(enabled=True))
    response = client.get("/")
    assert "X-Query-Count" in response.headers


def test_enabled_false_stays_off_even_in_debug(article):
    client = TestClient(_router(enabled=False))
    with override_settings(DEBUG=True):
        response = client.get("/")
    assert "X-Query-Count" not in response.headers


def test_query_plan_lists_the_chosen_prefetch(article):
    client = TestClient(_router(enabled=True))
    response = client.get("/?expand=comments")
    assert response.headers["X-Query-Plan"] == "prefetch_related=comments"
    assert response.headers["X-Query-Count"] == "2"


def test_no_plan_header_without_relations(article):
    client = TestClient(_router(enabled=True))
    response = client.get("/")
    assert "X-Query-Plan" not in response.headers


def test_never_leaks_sql_text(article):
    client = TestClient(_router(enabled=True))
    response = client.get("/?expand=comments")
    for value in response.headers.values():
        assert "SELECT" not in value.upper()


async def test_async_operations_still_get_the_headers():
    # The ORM access of an async list may run on a different, thread-local connection
    # (see the module docstring), so only the headers' presence and shape are checked here.
    ada = await User.objects.acreate(username="ada")
    await Article.objects.acreate(title="a1", slug="s1", author=ada)
    router = AsyncArticles.as_router()
    use_middleware(router, QueryExplainMiddleware(enabled=True))
    client = TestAsyncClient(router)
    response = await client.get("/")
    assert response.headers["X-Query-Count"].isdigit()
    assert float(response.headers["X-Query-Time"]) >= 0


def test_query_plan_attr_defaults_to_none():
    from django.test import RequestFactory

    request = RequestFactory().get("/x")
    assert request.__dict__.get(QUERY_PLAN_ATTR) is None
