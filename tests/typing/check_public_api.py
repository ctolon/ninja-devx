"""Static typing checks, run with mypy and pyright (not executed by pytest).

Lines marked ``# type: ignore[...]`` are negative checks: with ``warn_unused_ignores``
(mypy) and ``reportUnnecessaryTypeIgnoreComment`` (pyright) they fail if the error
they expect disappears.
"""

import time as time_module
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import ClassVar, Protocol, assert_type

from django.db.models import QuerySet
from django.http import HttpRequest
from ninja import Router, Schema, Status
from ninja.pagination import PageNumberPagination
from ninja.responses import codes_4xx
from ninja.streaming import SSE

from ninja_devx import (
    BasePermission,
    Container,
    Controller,
    ControllerOptions,
    IsAuthenticated,
    IsOwner,
    Resolver,
    Scope,
    get,
    post,
)
from ninja_devx.cqrs import QueryHandler
from ninja_devx.crud import CRUDController, Lookup, Patch, SearchBackend
from ninja_devx.testing.clients import client_for
from tests.testapp.models import Article


class Repository(ABC):
    @abstractmethod
    def names(self) -> list[str]: ...


class Clock(Protocol):
    def now(self) -> float: ...


class MemoryRepository(Repository):
    def names(self) -> list[str]:
        return ["ada"]


class UserOut(Schema):
    name: str


class AuthedRequest(HttpRequest):
    auth: str


class ArticleOnly(BasePermission[Article]):
    def has_object_permission(self, request: HttpRequest, obj: Article, /) -> bool:
        return obj.pk is not None


class UserController(Controller):
    scope = Scope.SINGLETON
    options = ControllerOptions(tags=("users",), permissions=[IsAuthenticated() & IsOwner()])

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @get("/", response=list[UserOut], summary="List users")
    def list_users(self, request: HttpRequest) -> list[str]:
        return self.repository.names()

    @post("/{user_id}", response={204: None, codes_4xx: UserOut}, permissions=[ArticleOnly()])
    async def touch(self, request: AuthedRequest, user_id: int) -> Status[None]:
        return Status(204, None)

    @get("/events", response=SSE[UserOut])
    async def events(self, request: HttpRequest) -> object:
        yield UserOut(name="ada")


# --- DI: abstract classes and protocols are valid keys for mypy and pyright ---
container = Container()
container.singleton(Repository, MemoryRepository)
container.scoped(Clock, lambda: __import__("time"))
assert_type(container.resolve(Repository), Repository)
assert_type(container.resolve(MemoryRepository), MemoryRepository)
with container.override(Repository, MemoryRepository()):  # no cast needed
    pass
container.instance(Clock, time_module)  # protocols work as keys for instances too
resolver: Resolver = container
assert_type(UserController.as_router(container=resolver, auth=None), Router)

# --- Decorated methods keep their signature ---
controller = UserController(MemoryRepository())
assert_type(controller.list_users(HttpRequest()), list[str])


# --- CRUD: generic arguments flow into hooks ---
class ArticleIn(Schema):
    title: str


class ArticleController(CRUDController[Article, UserOut, ArticleIn]):
    pagination_class = PageNumberPagination

    def get_queryset(self, request: HttpRequest) -> QuerySet[Article]:
        return Article.objects.filter(published=True)

    def perform_create(self, request: HttpRequest, payload: ArticleIn) -> Article:
        assert_type(payload.title, str)
        return Article()

    @get("/{pk}/title")
    def title(self, request: HttpRequest, pk: Lookup) -> str:
        article = self.get_object(request, pk)
        assert_type(article, Article)
        return str(article)

    def patched(self, data: Patch[ArticleIn]) -> None:
        assert_type(data, Patch[ArticleIn])


assert_type(ArticleController.get_model(), type[Article])
client_for(ArticleController, scope=Scope.REQUEST)

# --- Negative checks ---
get("/", summry="typo")  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
get("/", response=UserOut(name="instance"))  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
ControllerOptions(auth=123)  # type: ignore[typeddict-item]  # pyright: ignore[reportArgumentType]


class MissingRequest(Controller):
    @get("/")  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    def no_request(self) -> None: ...


# --- Layers, errors, async ------------------------------------------------------------------------
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

from django.contrib.auth.models import User
from ninja import NinjaAPI

from ninja_devx import (
    Also,
    DjangoModelPermissions,
    ErrorMap,
    Inject,
    IsReadOnly,
    IsStaff,
    LoggingHook,
    Mount,
    OperationHook,
    OperationInfo,
    Resolve,
    as_permission,
    async_variant,
    current_user,
    get_operation,
    idempotent,
    mount,
    request_context,
    use_case,
)
from ninja_devx import (
    AuthedRequest as Authed,
)
from ninja_devx import (
    ControllerOptions as Options,
)
from ninja_devx.codegen import generate_python, generate_typescript
from ninja_devx.crud import (
    AsyncCRUDController,
    BulkCreateMixin,
    Instance,
    Locked,
    ModelRepository,
    ModelService,
    Parent,
    SoftDeleteMixin,
)
from ninja_devx.crud import Repository as NoteRepositoryProtocol
from ninja_devx.layers import DomainError, NotFound, Policy, RequestContext
from ninja_devx.testing.clients import assert_max_queries
from tests.testapp.models import Comment, Note


class Timing:
    @contextmanager
    def around(self, request: HttpRequest, operation: OperationInfo, /) -> Generator[None]:
        yield


hook: OperationHook = Timing()


class NoteOut(Schema):
    id: int


class NoteIn(Schema):
    text: str


class NoteLocked(DomainError):
    http_status = 423
    code = "note_locked"


class NoteService(ModelService[Note]):
    pass


def client_ip(request: HttpRequest) -> str:
    return str(request.META.get("REMOTE_ADDR", ""))


class OwnsNote:
    def allows(self, subject: User, obj: Note, /) -> bool:
        return obj.pk is not None and subject.pk is not None


policy: Policy[User, Note] = OwnsNote()


class NoteController(
    SoftDeleteMixin[Note, NoteOut],
    BulkCreateMixin[Note, NoteOut, NoteIn],
    CRUDController[Note, NoteOut, NoteIn],
):
    owner_field = "owner"
    service_class = NoteService
    search_fields = ("text",)
    filter_fields = {"priority": ("gte", "lte")}
    options = Options(
        hooks=[LoggingHook(), hook],
        permissions=[DjangoModelPermissions()],
        decorators=[idempotent(ttl=60)],
        errors=ErrorMap().map(NoteLocked, 423),
    )

    @post("/{pk}/archive", response=NoteOut, permissions=Also(IsStaff()), atomic=True)
    def archive(self, request: Authed[User], note: Locked[Note]) -> Note:
        assert_type(request.auth, User)
        assert_type(note, Note)
        assert_type(current_user(request, User), User)
        assert_type(get_operation(request), OperationInfo | None)
        return note

    @get("/{pk}/peek", response=NoteOut, permissions=[IsReadOnly()])
    def peek(
        self,
        request: HttpRequest,
        note: Instance[Note],
        service: Inject[NoteService],
        ip: Annotated[str, Resolve(client_ip)],
    ) -> Note:
        assert_type(service, NoteService)
        assert_type(ip, str)
        context = request_context(User)(request)
        assert_type(context.user, User)
        return note

    @async_variant(peek)
    async def apeek(self, request: HttpRequest, note: Instance[Note]) -> Note:
        return note


as_permission(policy, User)
assert_type(RequestContext(user=User(), tenant=None).tenant, None)


class CommentController(AsyncCRUDController[Comment, NoteOut, NoteIn]):
    parent = Parent(Article, field="article")


class Archive:
    def __call__(self, command: int, /) -> Note:
        raise NotFound("note")


class Commands(Controller):
    archive = use_case(post("/archive"), Archive, command=int)


repository: NoteRepositoryProtocol[Note] = ModelRepository[Note]()
container.scoped(NoteRepositoryProtocol[Note], ModelRepository[Note])

api = NinjaAPI()
routers = mount(
    api, {"/notes": NoteController, "/comments": Mount(CommentController)}, prefix="/v1"
)
assert_type(routers, dict[str, Router])
assert_type(generate_python(api.get_openapi_schema()), str)
assert_type(generate_typescript(api.get_openapi_schema()), str)
with assert_max_queries(3) as queries:
    assert_type(len(queries.captured_queries), int)

# --- SaaS, HTTP and configuration ---
from decimal import Decimal

from ninja_devx import ETag, FieldVisibility, RouteOptions, VisibleTo, current_tenant
from ninja_devx.crud import CursorPagination, SoftDelete
from ninja_devx.http.conditional import conditional
from ninja_devx.http.throttling import ClientRateThrottle, ScopedRateThrottle, TenantRateThrottle


class SalaryOut(FieldVisibility, Schema):
    id: int
    salary: Annotated[Decimal | None, VisibleTo(IsStaff(), hidden="omit")] = None


def org_of(request: HttpRequest) -> object:
    return getattr(request, "tenant", None)


class TenantNotes(SoftDeleteMixin[Note, NoteOut], CRUDController[Note, NoteOut, NoteIn]):
    tenant_field = "owner"
    tenant_resolver = staticmethod(org_of)
    etag = ETag(field="priority", require_if_match=True)
    soft_delete = SoftDelete("deleted_at", deleted_by=None)
    pagination_class = CursorPagination
    pagination_options = {"page_size": 20}
    lookup_param = "note_id"
    ordering_param = "sort"
    routes: ClassVar[Mapping[str, RouteOptions]] = {
        "destroy": {"enabled": False},
        "list": {"throttle": [ScopedRateThrottle("notes"), ClientRateThrottle(anon="5/min")]},
    }

    @get("/{pk}/stats", decorators=[conditional()], throttle=[TenantRateThrottle("100/min")])
    def stats(self, request: HttpRequest, pk: int) -> dict[str, object]:
        return {"tenant": current_tenant(request)}


assert_type(ETag().require_if_match, bool)

# --- Negative checks (layers, errors, async) ---
mount(api, {"/x": 123})  # type: ignore[dict-item]  # pyright: ignore[reportArgumentType]
Parent(Article, field=1)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
ErrorMap().map(int, 400)  # type: ignore[type-var]  # pyright: ignore[reportArgumentType]


class ArticleSearch:
    def search(
        self, queryset: QuerySet[Article], term: str, fields: Sequence[str], /
    ) -> QuerySet[Article]:
        return queryset


class CountArticles:
    def __call__(self, query: object, /) -> int:
        return 0


search_backend: SearchBackend[Article] = ArticleSearch()
query_handler: QueryHandler[object, int] = CountArticles()
