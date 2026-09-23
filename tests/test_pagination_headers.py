from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory
from ninja import NinjaAPI
from ninja.testing import TestClient

from ninja_devx.crud import CursorPagination, LimitOffsetPagination, ReadOnlyModelController
from ninja_devx.http.middleware import use_middleware
from ninja_devx.http.pagination_headers import PAGINATION_ATTR, PaginationHeadersMiddleware
from tests.testapp.api import ArticleOut
from tests.testapp.models import Article

_rf = RequestFactory()


def make_articles(count: int) -> None:
    ada = User.objects.create(username="ada")
    for index in range(count):
        Article.objects.create(title=f"A{index}", slug=f"a{index}", author=ada)


def test_limit_offset_sets_total_count_and_link_headers(db):
    make_articles(5)

    class Articles(ReadOnlyModelController[Article, ArticleOut]):
        pagination_class = LimitOffsetPagination
        pagination_options = {"limit": 2}

    api = NinjaAPI()
    api.add_router("/articles", Articles.as_router())
    use_middleware(api, PaginationHeadersMiddleware())
    response = TestClient(api).get("/articles/")
    assert response["X-Total-Count"] == "5"
    assert 'rel="next"' in response["Link"]


def test_cursor_pagination_sets_link_headers_without_a_count(db):
    make_articles(3)

    class Articles(ReadOnlyModelController[Article, ArticleOut]):
        pagination_class = CursorPagination
        pagination_options = {"page_size": 2}

    api = NinjaAPI()
    api.add_router("/articles", Articles.as_router())
    use_middleware(api, PaginationHeadersMiddleware())
    response = TestClient(api).get("/articles/")
    assert 'rel="next"' in response["Link"]
    assert "X-Total-Count" not in response.headers


def test_pagination_headers_skip_missing_metadata_and_existing_headers():
    middleware = PaginationHeadersMiddleware()
    response = HttpResponse(b"{}")
    assert middleware.process_response(_rf.get("/x"), response) is response
    assert "Link" not in response

    request = _rf.get("/x")
    request.__dict__[PAGINATION_ATTR] = {"count": None, "next": None, "previous": None}
    assert middleware.process_response(request, response) is response
    assert "X-Total-Count" not in response.headers

    request.__dict__[PAGINATION_ATTR] = {
        "count": 3,
        "next": "http://x?page=2",
        "previous": "http://x?page=0",
    }
    response["X-Total-Count"] = "30"
    response["Link"] = '<http://x?page=9>; rel="next"'
    out = middleware.process_response(request, response)
    assert out["X-Total-Count"] == "30"
    assert out["Link"] == '<http://x?page=9>; rel="next"'
