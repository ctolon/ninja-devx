import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import ModuleType
from uuid import uuid4

import django
import pytest
from asgiref.testing import ApplicationCommunicator
from django.core.handlers.asgi import ASGIHandler
from django.http import Http404
from django.test import override_settings
from django.urls import path
from ninja import NinjaAPI, Schema
from ninja.streaming import SSE

from ninja_devx import BasePermission, Container, Controller, ControllerOptions, get


class Event(Schema):
    value: int


class HeaderPermission(BasePermission):
    def has_permission(self, request):
        return request.headers.get("X-Deny") != "1"


def scope(headers):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/events",
        "raw_path": b"/events",
        "query_string": b"",
        "headers": headers,
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    }


def authentication(request):
    return request.headers.get("Authorization")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ([], 401),
        ([(b"authorization", b"ok"), (b"x-deny", b"1")], 403),
        ([(b"authorization", b"ok"), (b"x-missing", b"1")], 404),
    ],
)
async def test_denials_are_http_errors_before_stream_headers(headers, status):
    produced = []

    class Events(Controller):
        options = ControllerOptions(auth=authentication, permissions=[HeaderPermission()])

        def before_operation(self, request, operation):
            if request.headers.get("X-Missing"):
                raise Http404("not found")

        @get("/events", response=SSE[Event])
        async def events(self, request):
            produced.append(True)
            yield Event(value=1)

    api = NinjaAPI(urls_namespace=f"preflight-{uuid4().hex}")
    api.add_router("", Events.as_router())
    urls = ModuleType("stream_test_urls")
    urls.urlpatterns = [path("", api.urls)]
    with override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[]):
        communicator = ApplicationCommunicator(ASGIHandler(), scope(headers))
        await communicator.send_input({"type": "http.request", "body": b""})
        start = await communicator.receive_output(timeout=2)
        assert start["type"] == "http.response.start"
        assert start["status"] == status
        body = await communicator.receive_output(timeout=2)
        assert body["type"] == "http.response.body"
        assert b"detail" in body["body"]
        await communicator.wait(timeout=2)
    assert produced == []


async def test_disconnect_closes_hook_and_di_in_their_original_task_context():
    context = ContextVar("stream-context", default="outside")
    closed = asyncio.Event()
    cleanup = []
    tasks = []

    class Resource:
        pass

    async def resource():
        tasks.append(asyncio.current_task())
        try:
            yield Resource()
        finally:
            assert context.get() == "inside"
            tasks.append(asyncio.current_task())
            cleanup.append("resource")

    class Hook:
        @asynccontextmanager
        async def around_async(self, request, operation):
            token = context.set("inside")
            try:
                yield
            finally:
                context.reset(token)
                cleanup.append("hook")
                closed.set()

    class Events(Controller):
        options = ControllerOptions(hooks=[Hook()])

        def __init__(self, resource: Resource):
            self.resource = resource

        @get("/events", response=SSE[Event])
        async def events(self, request):
            assert context.get() == "inside"
            await asyncio.Event().wait()
            yield Event(value=1)

    body_files = []

    class TrackingHandler(ASGIHandler):
        async def read_body(self, receive):
            body_file = await super().read_body(receive)
            body_files.append(body_file)
            return body_file

    container = Container()
    container.scoped(Resource, resource)
    api = NinjaAPI(urls_namespace=f"cancel-{uuid4().hex}")
    api.add_router("", Events.as_router(container=container))
    urls = ModuleType("stream_test_urls")
    urls.urlpatterns = [path("", api.urls)]
    with override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[]):
        communicator = ApplicationCommunicator(TrackingHandler(), scope([]))
        await communicator.send_input({"type": "http.request", "body": b""})
        start = await communicator.receive_output(timeout=2)
        assert start["status"] == 200  # Does not wait for the first event.
        if django.VERSION < (5, 0):
            # Django 4.2 does not listen for a peer disconnect while sending a stream.
            # Exercise server-driven application cancellation on that version instead.
            communicator.stop(exceptions=False)
        else:
            await communicator.send_input({"type": "http.disconnect"})
        await communicator.wait(timeout=2)
        await asyncio.wait_for(closed.wait(), timeout=2)
    assert body_files
    assert all(body.closed for body in body_files)
    assert cleanup == ["resource", "hook"]
    assert tasks[0] is tasks[1]
    assert context.get() == "outside"
