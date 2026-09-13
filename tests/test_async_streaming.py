import json

from ninja import Schema
from ninja.streaming import JSONL, SSE
from ninja.testing import TestAsyncClient, TestClient

from ninja_devx import Container, Controller, Scope, get


class Event(Schema):
    n: int


class EventSource:
    def events(self, count: int):
        return [Event(n=n) for n in range(count)]


class EventController(Controller):
    def __init__(self, source: EventSource) -> None:
        self.source = source

    @get("/one", response=Event)
    async def one(self, request):
        return self.source.events(1)[0]

    @get("/sse", response=SSE[Event])
    async def sse(self, request, count: int = 2):
        for event in self.source.events(count):
            yield event

    @get("/jsonl", response=JSONL[Event])
    def jsonl(self, request, count: int = 2):
        yield from self.source.events(count)


def router(scope=Scope.REQUEST):
    return EventController.as_router(container=Container(), scope=scope)


async def test_coroutine_operation():
    for scope in Scope:
        response = await TestAsyncClient(router(scope)).get("/one")
        assert response.json() == {"n": 0}


def test_handlers_keep_their_sync_or_async_nature():
    operations = {path: view.operations[0] for path, view in router().path_operations.items()}
    assert operations["/one"].is_async
    assert operations["/sse"].is_async
    assert not operations["/jsonl"].is_async


async def test_async_generator_server_sent_events():
    response = await TestAsyncClient(router()).get("/sse?count=3")
    assert response.streaming
    assert response.content.decode() == "".join(
        f"data: {json.dumps({'n': n})}\n\n" for n in range(3)
    )


def test_sync_generator_json_lines():
    response = TestClient(router()).get("/jsonl?count=2")
    assert response.streaming
    lines = response.content.decode().splitlines()
    assert [json.loads(line) for line in lines] == [{"n": 0}, {"n": 1}]
