import pytest

from ninja_devx.contrib.tasks import (
    CeleryTaskQueue,
    DeferredTaskQueue,
    DramatiqTaskQueue,
    FastStreamTaskQueue,
    FrameworkTaskQueue,
    RQTaskQueue,
    TaskiqTaskQueue,
    TemporalTaskQueue,
)


class CeleryTask:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def delay(self, *args: object, **kwargs: object) -> str:
        self.calls.append((args, kwargs))
        return "celery"


class DramatiqActor:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def send(self, *args: object, **kwargs: object) -> str:
        self.calls.append((args, kwargs))
        return "dramatiq"


class TaskiqTask:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def kiq(self, *args: object, **kwargs: object) -> str:
        self.calls.append((args, kwargs))
        return "taskiq"


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def enqueue(self, function: object, *args: object, **kwargs: object) -> str:
        self.calls.append((function, args, kwargs))
        return "rq"


class FakeTemporal:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def start_workflow(self, workflow: object, *args: object, **kwargs: object) -> str:
        self.calls.append((workflow, args, kwargs))
        return "temporal"


class CeleryTaskWithOptions(CeleryTask):
    def apply_async(self, args: object = None, kwargs: object = None) -> str:
        raise AssertionError("delay is the Celery entry point")


def test_celery_detects_delay():
    task = CeleryTask()
    assert CeleryTaskQueue().call(task, 1, key="v") is None
    assert task.calls == [((1,), {"key": "v"})]
    with_options = CeleryTaskWithOptions()
    CeleryTaskQueue().enqueue(with_options, 2)
    assert with_options.calls == [((2,), {})]


def test_dramatiq_and_taskiq_detect_their_methods():
    actor = DramatiqActor()
    DramatiqTaskQueue().call(actor)
    assert actor.calls == [((), {})]
    task = TaskiqTask()
    TaskiqTaskQueue().call(task, "x")
    assert task.calls == [(("x",), {})]


def test_rq_and_temporal_use_the_injected_dependency():
    queue = FakeQueue()
    RQTaskQueue(queue).call(print, 1)
    assert queue.calls == [(print, (1,), {})]
    client = FakeTemporal()
    TemporalTaskQueue(client).call(print, 2)
    assert client.calls == [(print, (2,), {})]


def test_enqueue_uses_the_same_transport_as_call():
    queue = FakeQueue()
    FrameworkTaskQueue(queue=queue).enqueue(print, 3)
    assert queue.calls == [(print, (3,), {})]
    deferred: list[object] = []
    DeferredTaskQueue(lambda f, a, k: deferred.append((f, a, k))).enqueue(print, 4)
    assert deferred == [(print, (4,), {})]


def test_unknown_target_raises():
    with pytest.raises(TypeError, match="cannot be deferred"):
        FrameworkTaskQueue().call(object())


class FakeBroker:
    def __init__(self) -> None:
        self.messages: list[tuple[object, dict[str, object]]] = []

    async def publish(self, message: object, **kwargs: object) -> str:
        self.messages.append((message, kwargs))
        return "published"


def test_faststream_publishes_a_task_message():
    broker = FakeBroker()

    def notify(order_id: int, *, urgent: bool = False) -> None: ...

    queue = FastStreamTaskQueue(broker, queue="tasks")
    queue.call(notify, 7, urgent=True)
    queue.enqueue(notify, 8)
    message, kwargs = broker.messages[0]
    assert broker.messages[1][0] == {"task": "notify", "args": [8], "kwargs": {}}
    assert message == {"task": "notify", "args": [7], "kwargs": {"urgent": True}}
    assert kwargs == {"queue": "tasks"}


def test_faststream_without_a_queue():
    broker = FakeBroker()
    FastStreamTaskQueue(broker).call(print, "x")
    assert broker.messages[0][1] == {}


def test_deferred_task_queue_adapts_any_transport():
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []
    queue = DeferredTaskQueue(lambda f, a, k: calls.append((f, a, k)))
    queue.call(print, 1, flag=True)
    assert calls == [(print, (1,), {"flag": True})]
