"""An API with nested routers and plain controllers for the inspect tests (``CHECK_APIS``)."""

from ninja import NinjaAPI, Router, Schema

from ninja_devx import Controller, ControllerOptions, IsAuthenticated, get, post
from ninja_devx.crud import ReadOnlyModelController
from ninja_devx.http.errors import ErrorMap
from ninja_devx.idempotency import idempotent
from tests.testapp.api import ArticleController
from tests.testapp.models import Article, Comment, Note, Task

api = NinjaAPI(urls_namespace="inspect-api")


class Widgets(Controller):
    options = ControllerOptions(
        permissions=[IsAuthenticated()], atomic="durable", database="default"
    )
    routes = {"remove": {"enabled": False}}

    @get("/", permissions=[IsAuthenticated()], errors=ErrorMap.django_defaults(), atomic="durable")
    def index(self, request):
        return {"ok": True}

    @post("/", decorators=[idempotent()])
    def create(self, request):
        return {"ok": True}

    @get("/slow", atomic=False)
    async def slow(self, request):
        return {"ok": True}

    @get("/remove")
    def remove(self, request):
        return {"ok": True}


class ArticleBrief(Schema):
    id: int
    title: str


class CommentOut(Schema):
    id: int
    body: str
    article: ArticleBrief


class Comments(ReadOnlyModelController[Comment, CommentOut]):
    pass


class NoteOut(Schema):
    id: int
    text: str


class UnsafeOwnerNotes(ReadOnlyModelController[Note, NoteOut]):
    """Sets ``owner_field`` but drops the automatic ``IsOwner`` by skipping ``super()``."""

    owner_field = "owner"

    @classmethod
    def merged_options(cls, overrides=None):
        return overrides or {}


class TaskOut(Schema):
    id: int
    title: str


class TenantlessTasks(ReadOnlyModelController[Task, TaskOut]):
    tenant_field = "project"


class ArticleSummaryOut(Schema):
    id: int
    title: str
    comment_count: int

    @staticmethod
    def resolve_comment_count(obj: Article) -> int:
        return obj.comments.count()


class ArticleSummaries(ReadOnlyModelController[Article, ArticleSummaryOut]):
    pass


nested = Router()
inner = Router()


@inner.get("/deep")
def deep(request):
    return {"ok": True}


nested.add_router("/inner", inner)


@nested.get("/ping")
def ping(request):
    return {"ok": True}


api.add_router("/nested", nested, url_name_prefix="n1")
api.add_router("/nested-again", nested, url_name_prefix="n2")
api.add_router("/widgets", Widgets.as_router())
api.add_router("/comments", Comments.as_router())
api.add_router("/articles", ArticleController.as_router())
api.add_router("/unsafe-owner-notes", UnsafeOwnerNotes.as_router())
api.add_router("/tenantless-tasks", TenantlessTasks.as_router())
api.add_router("/article-summaries", ArticleSummaries.as_router())
