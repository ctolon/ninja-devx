"""A realistic CRUD setup shared by the CRUD, permission and OpenAPI tests."""

from datetime import datetime
from typing import Annotated

from django.contrib.auth.models import User
from django.db.models import QuerySet
from django.http import HttpRequest
from ninja import FilterLookup, FilterSchema, Schema
from ninja.pagination import PageNumberPagination

from ninja_devx import Controller, ControllerOptions, IsAuthenticated, IsOwner, Scope, get
from ninja_devx.crud import CRUDController, Lookup, save_instance

from .models import Article


def current_user(request: HttpRequest) -> User:
    user: object = getattr(request, "auth", None)
    assert isinstance(user, User)
    return user


def user_auth(request: HttpRequest) -> User | None:
    username = request.headers.get("X-User")
    return User.objects.filter(username=username).first() if username else None


class ArticleIn(Schema):
    title: str
    slug: str
    body: str = ""
    tags: list[int] = []


class ArticleOut(Schema):
    id: int
    title: str
    slug: str
    body: str
    author_id: int
    tags: list[int]
    published: bool
    created: datetime

    @staticmethod
    def resolve_tags(obj: Article) -> list[int]:
        return [tag.pk for tag in obj.tags.all()]


class ArticleFilters(FilterSchema):
    search: Annotated[str | None, FilterLookup(["title__icontains", "body__icontains"])] = None
    published: bool | None = None


class ArticleController(CRUDController[Article, ArticleOut, ArticleIn]):
    options = ControllerOptions(
        auth=user_auth,
        tags=["articles"],
        permissions=[IsAuthenticated(), IsOwner("author")],
    )
    filter_schema = ArticleFilters
    ordering_fields = ("created", "title")
    default_ordering = ("-created",)
    pagination_class = PageNumberPagination

    def get_queryset(self, request: HttpRequest) -> QuerySet[Article]:
        return Article.objects.select_related("author").prefetch_related("tags")

    def perform_create(self, request: HttpRequest, payload: ArticleIn) -> Article:
        return save_instance(Article(author=current_user(request)), payload.model_dump())

    @get("/{pk}/summary", response=dict[str, str])
    def summary(self, request: HttpRequest, pk: Lookup) -> dict[str, str]:
        article = self.get_object(request, pk)
        return {"title": article.title}


class SlugArticleController(CRUDController[Article, ArticleOut, ArticleIn]):
    scope = Scope.SINGLETON
    lookup_field = "slug"
    options = ControllerOptions(auth=user_auth)

    def perform_create(self, request: HttpRequest, payload: ArticleIn) -> Article:
        return save_instance(Article(author=current_user(request)), payload.model_dump())


class PingController(Controller):
    @get("/")
    def ping(self, request: HttpRequest) -> dict[str, bool]:
        return {"ok": True}
