import asyncio

import pytest
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest

from ninja_devx import Container
from ninja_devx.layers import (
    ImmediateTaskQueue,
    ModelRepository,
    ModelService,
    NotFound,
    OnCommitTaskQueue,
    PolicyDenied,
    RecordingTaskQueue,
    Repository,
    RequestContext,
    ValidationFailed,
    after_commit,
    allowed,
    dual,
    require,
)
from ninja_devx.layers.testing import InMemoryRepository, make_context
from ninja_devx.security.auth import arequest_context, request_context
from tests.testapp.models import Tag


class Greeter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    @dual
    def greet(self, name: str) -> str:
        self.calls.append("sync")
        return f"hello {name}"


class NativeGreeter(Greeter):
    @dual
    def greet(self, name: str) -> str:
        return f"hello {name}"

    @greet.native
    async def _agreet(self, name: str) -> str:
        await asyncio.sleep(0)
        return f"native hello {name}"


async def test_dual_methods():
    greeter = Greeter()
    assert greeter.greet("ada") == "hello ada"
    assert await greeter.greet.a("bob") == "hello bob"
    assert greeter.calls == ["sync", "sync"]
    assert not greeter.greet.has_native

    native = NativeGreeter()
    assert native.greet("ada") == "hello ada"
    assert await native.greet.a("ada") == "native hello ada"
    assert native.greet.has_native
    assert isinstance(Greeter.__dict__["greet"], dual)


@pytest.mark.django_db
def test_model_repository_and_service():
    class TagRepository(ModelRepository[Tag]):
        pass

    repository = TagRepository()
    service = ModelService(repository)
    tag = service.create({"name": "python"})
    assert repository.get(tag.pk).name == "python"
    assert service.update(tag, {"name": "django"}).name == "django"
    with pytest.raises(ValidationFailed) as exc_info:
        service.create({"name": "django"})
    assert exc_info.value.errors == {"name": ["Tag with this Name already exists."]}
    service.delete(tag)
    with pytest.raises(NotFound, match="Tag not found"):
        repository.get(tag.pk)
    with pytest.raises(TypeError, match="needs a model"):
        ModelRepository()


@pytest.mark.django_db(transaction=True)
async def test_async_repository_methods():
    repository = ModelRepository(Tag)
    tag = await repository.aadd({"name": "async"})
    assert (await repository.aget("async", field="name")).pk == tag.pk
    await repository.achange(tag, {"name": "renamed"})
    await repository.aremove(tag)
    with pytest.raises(NotFound):
        await repository.aget(tag.pk)
    service = ModelService(model=Tag)
    created = await service.create.a({"name": "via service"})
    assert created.pk is not None


def test_in_memory_repository_serves_services_without_a_database():
    service = ModelService(InMemoryRepository(Tag))
    tag = service.create({"name": "memory"})
    assert tag.pk == 1
    assert service.repository.get(1) is tag


@pytest.mark.django_db
def test_services_get_repositories_from_the_container():
    class TagService(ModelService[Tag]):
        pass

    container = Container()
    container.singleton(Repository[Tag], lambda: InMemoryRepository(Tag))
    service = container.resolve(TagService)
    assert isinstance(service.repository, InMemoryRepository)
    assert isinstance(Container().resolve(TagService).repository, ModelRepository)


@pytest.mark.django_db
def test_task_queues():
    ran: list[int] = []
    queue = OnCommitTaskQueue()
    with transaction.atomic():
        queue.call(ran.append, 1)
        assert ran == []
    # pytest-django wraps tests in a transaction: flush on-commit callbacks explicitly.
    recording = RecordingTaskQueue()
    recording.call(ran.append, 2)
    assert ran == []
    assert len(recording.calls) == 1
    recording.run_all()
    assert ran == [2]
    ImmediateTaskQueue().call(ran.append, 3)
    assert ran == [2, 3]


def test_after_commit_runs_immediately_outside_transactions(django_capture_on_commit_callbacks, db):
    ran: list[str] = []
    with django_capture_on_commit_callbacks(execute=True):
        after_commit(lambda: ran.append("done"))
    assert ran == ["done"]


def test_policies():
    class CanEdit:
        message = "Only authors can edit."

        def allows(self, subject: str, obj: dict[str, str]) -> bool:
            return obj["author"] == subject

    post = {"author": "ada"}
    assert allowed(CanEdit(), "ada", post)
    with pytest.raises(PolicyDenied, match="Only authors") as exc_info:
        require(CanEdit(), "bob", post)
    assert exc_info.value.http_status == 403


def test_make_context():
    context = make_context("ada", None, source="test")
    assert (context.user, context.tenant, context.metadata) == ("ada", None, {"source": "test"})
    assert len(context.request_id) == 32


@pytest.mark.django_db
def test_request_context_factories():
    user = User.objects.create(username="ada")
    request = HttpRequest()
    request.user = user
    request.META["HTTP_X_REQUEST_ID"] = "req-1"
    request.META["HTTP_TRACEPARENT"] = "00-abc123-def-01"

    container = Container()
    container.scoped(RequestContext[User, None], request_context(User))
    with container.request_scope(request) as scope:
        context = scope.resolve(RequestContext[User, None])
    assert (context.user, context.request_id, context.trace_id) == (user, "req-1", "abc123")

    def tenant_of(request: HttpRequest, user: User) -> str:
        return f"org-of-{user.username}"

    with_tenant = request_context(User, tenant=tenant_of)(request)
    assert with_tenant.tenant == "org-of-ada"


@pytest.mark.django_db(transaction=True)
async def test_async_request_context_loads_session_users():
    user = await User.objects.acreate(username="async-ada")
    request = HttpRequest()

    async def auser() -> User:
        return user

    request.auser = auser  # type: ignore[method-assign]
    from django.utils.functional import SimpleLazyObject

    request.user = SimpleLazyObject(lambda: user)  # type: ignore[assignment]
    context = await arequest_context(User)(request)
    assert context.user == user


def test_services_are_http_free():
    import subprocess
    import sys

    code = (
        "import sys, django; from django.conf import settings; "
        "settings.configure(); django.setup(); "
        "import ninja_devx.layers; assert 'ninja' not in sys.modules, 'layers imported ninja'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.django_db
def test_captured_commits_runs_on_commit_work(captured_commits):
    from django.db import transaction

    from ninja_devx.layers import OnCommitTaskQueue

    ran: list[str] = []
    with captured_commits() as callbacks:
        with transaction.atomic():
            OnCommitTaskQueue().call(ran.append, "sent")
        assert ran == []
    assert len(callbacks) == 1
    assert ran == ["sent"]
