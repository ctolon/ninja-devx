from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from ninja import Schema
from ninja.testing import TestClient

from ninja_devx.crud import (
    ReadOnlyModelController,
    optimize_queryset,
    related_lookups,
    requires_related,
)
from tests.testapp.models import Article, Comment


class UserOut(Schema):
    id: int
    username: str


class ArticleBrief(Schema):
    id: int
    title: str
    author: UserOut


class CommentOut(Schema):
    id: int
    body: str
    article: ArticleBrief


class ArticleWithComments(Schema):
    id: int
    title: str
    author: UserOut
    comments: list[CommentOut]


class ArticleWithBody(Schema):
    id: int
    title: str
    body_length: int

    @staticmethod
    def resolve_body_length(obj: Article) -> int:
        return len(obj.body)


def make_articles(count: int) -> None:
    ada = User.objects.create(username="ada")
    for index in range(count):
        article = Article.objects.create(
            title=f"A{index}", slug=f"a{index}", author=ada, body="x" * 500
        )
        Comment.objects.create(article=article, body="first")
        Comment.objects.create(article=article, body="second")


def test_reverse_relations_join_their_foreign_keys(db):
    assert related_lookups(Article, ArticleWithComments) == (
        ("author",),
        ("comments", "comments__article", "comments__article__author"),
    )


def test_nested_foreign_keys_inside_prefetches_cost_no_extra_queries(db):
    make_articles(5)

    class Articles(ReadOnlyModelController[Article, ArticleWithComments]):
        pass

    client = TestClient(Articles.as_router())
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/")
    # articles + author (joined), comments + article + author (joined)
    assert len(queries.captured_queries) == 2
    assert response.json()[0]["comments"][1]["article"]["author"]["username"] == "ada"


def test_only_restricts_columns_to_the_schema(db):
    make_articles(2)

    class Articles(ReadOnlyModelController[Article, ArticleWithComments]):
        optimize_queries = "only"

    client = TestClient(Articles.as_router())
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/")
    assert len(queries.captured_queries) == 2
    article_sql = queries.captured_queries[0]["sql"]
    assert '"testapp_article"."body"' not in article_sql
    assert '"testapp_article"."slug"' not in article_sql
    assert response.json()[1]["comments"][0]["body"] == "first"


def test_only_is_skipped_when_the_schema_reads_computed_values(db):
    make_articles(2)
    queryset = optimize_queryset(Article.objects.all(), ArticleWithBody, only=True)
    assert queryset.query.deferred_loading == (frozenset(), True)
    with CaptureQueriesContext(connection) as queries:
        lengths = [len(article.body) for article in queryset]
    assert lengths == [500, 500]
    assert len(queries.captured_queries) == 1


class ArticleAuthorName(Schema):
    id: int
    author_name: str

    @staticmethod
    @requires_related("author")
    def resolve_author_name(obj: Article) -> str:
        return obj.author.username


def test_requires_related_loads_resolver_relations(db):
    select, _ = related_lookups(Article, ArticleAuthorName)
    assert "author" in select


def test_controller_related_hint_avoids_n_plus_one(db):
    make_articles(5)

    class Articles(ReadOnlyModelController[Article, ArticleAuthorName]):
        related = ("author",)

    client = TestClient(Articles.as_router())
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/")
    assert len(queries.captured_queries) == 1
    assert response.json()[0]["author_name"] == "ada"


def test_related_hint_is_checked(db):
    class Articles(ReadOnlyModelController[Article, ArticleBrief]):
        related = ("nope",)

    assert "ninja_devx.W006" in [message.id for message in Articles.checks()]
