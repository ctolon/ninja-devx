from typing import Annotated

import pytest
from django.contrib.auth.models import User
from django.db.models import Q
from ninja import Schema
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import ControllerConfigError
from ninja_devx.crud import AsyncReadOnlyModelController, ReadOnlyModelController
from ninja_devx.crud.optimization import ExpandRule
from ninja_devx.serialization.visibility import Expandable, FieldVisibility
from ninja_devx.testing.clients import assert_max_queries
from tests.testapp.models import Article, Comment, Tag

pytestmark = pytest.mark.django_db(transaction=True)


class CommentOut(Schema):
    id: int
    body: str


class TagOut(Schema):
    id: int
    name: str


class ArticleOut(FieldVisibility, Schema):
    id: int
    title: str
    comments: Annotated[list[CommentOut] | None, Expandable()] = None
    tags: Annotated[list[TagOut] | None, Expandable()] = None


class Articles(ReadOnlyModelController[Article, ArticleOut]):
    expand_rules = {"comments": ExpandRule(order_by=("-id",), limit=2)}


class AsyncArticles(AsyncReadOnlyModelController[Article, ArticleOut]):
    expand_rules = {"comments": ExpandRule(order_by=("-id",), limit=2)}


@pytest.fixture
def articles():
    ada = User.objects.create(username="ada")
    a1 = Article.objects.create(title="a1", slug="a1", author=ada)
    a2 = Article.objects.create(title="a2", slug="a2", author=ada)
    for i in range(5):
        Comment.objects.create(article=a1, body=f"c{i}")
    for i in range(1):
        Comment.objects.create(article=a2, body=f"d{i}")
    return a1, a2


def test_limit_caps_prefetched_rows_per_parent(articles):
    a1, a2 = articles
    client = TestClient(Articles.as_router())
    with assert_max_queries(2):  # articles + comments (prefetched, ranked per article)
        data = client.get("/?expand=comments").json()
    by_id = {item["id"]: item["comments"] for item in data}
    assert [c["id"] for c in by_id[a1.pk]] == list(
        a1.comments.order_by("-id").values_list("pk", flat=True)[:2]
    )
    assert [c["id"] for c in by_id[a2.pk]] == list(
        a2.comments.order_by("-id").values_list("pk", flat=True)
    )


def test_filter_is_applied_before_limiting(articles):
    a1, _ = articles
    Comment.objects.create(article=a1, body="skip-this-one")

    class Filtered(ReadOnlyModelController[Article, ArticleOut]):
        expand_rules = {
            "comments": ExpandRule(filter=~Q(body__startswith="skip"), order_by=("-id",), limit=10)
        }

    client = TestClient(Filtered.as_router())
    data = client.get("/?expand=comments").json()
    by_id = {item["id"]: item["comments"] for item in data}
    assert all(not comment["body"].startswith("skip") for comment in by_id[a1.pk])
    assert len(by_id[a1.pk]) == 5


def test_default_ordering_falls_back_to_model_meta(articles):
    a1, _ = articles

    class Ordered(ReadOnlyModelController[Article, ArticleOut]):
        expand_rules = {"comments": ExpandRule(limit=2)}

    client = TestClient(Ordered.as_router())
    data = client.get("/?expand=comments").json()
    by_id = {item["id"]: item["comments"] for item in data}
    assert [c["id"] for c in by_id[a1.pk]] == list(
        a1.comments.order_by("id").values_list("pk", flat=True)[:2]
    )


async def test_async_list_applies_expand_rules():
    ada = await User.objects.acreate(username="ada")
    article = await Article.objects.acreate(title="a1", slug="a1", author=ada)
    for i in range(5):
        await Comment.objects.acreate(article=article, body=f"c{i}")
    client = TestAsyncClient(AsyncArticles.as_router())
    response = await client.get("/?expand=comments")
    comments = response.json()[0]["comments"]
    assert [c["id"] for c in comments] == list(
        [pk async for pk in article.comments.order_by("-id").values_list("pk", flat=True)][:2]
    )


def test_unknown_expand_rules_key_is_a_controller_config_error():
    class Bad(ReadOnlyModelController[Article, ArticleOut]):
        expand_rules = {"nope": ExpandRule()}

    with pytest.raises(ControllerConfigError, match="nope"):
        Bad.as_router()


def test_limit_on_a_many_to_many_relation_warns_and_is_unlimited(articles):
    a1, _ = articles
    python, django = Tag.objects.create(name="python"), Tag.objects.create(name="django")
    a1.tags.set([python, django])

    class Tagged(ReadOnlyModelController[Article, ArticleOut]):
        expand_rules = {"tags": ExpandRule(limit=1)}

    assert "ninja_devx.W007" in [message.id for message in Tagged.checks()]
    client = TestClient(Tagged.as_router())
    data = client.get("/?expand=tags").json()
    assert {tag["name"] for tag in data[0]["tags"]} == {"python", "django"}
