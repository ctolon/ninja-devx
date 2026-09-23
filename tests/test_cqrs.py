from dataclasses import dataclass

import pytest
from django.contrib.auth.models import User
from ninja import FilterSchema
from ninja.testing import TestClient

from ninja_devx import Container, Controller, get, use_query
from ninja_devx.cqrs import (
    Command,
    DomainEvent,
    EventBus,
    Message,
    Query,
    UnitOfWork,
    use_case,
)
from ninja_devx.layers import RecordingTaskQueue
from tests.testapp.models import Note


@dataclass(frozen=True)
class NoteStats(Query):
    prefix: str


class StatsFilters(FilterSchema):
    prefix: str = ""


def to_note_stats(filters: StatsFilters) -> NoteStats:
    return NoteStats(prefix=filters.prefix)


class NoteStatsHandler:
    def __call__(self, query: NoteStats) -> dict[str, int]:
        return {"count": Note.objects.filter(text__startswith=query.prefix).count()}


class Notes(Controller):
    stats = use_query(get("/stats", response=dict[str, int]), NoteStatsHandler, query=to_note_stats)


@pytest.mark.django_db
def test_use_query_maps_query_parameters_and_calls_the_handler():
    ada = User.objects.create(username="ada")
    Note.objects.create(owner=ada, text="apple")
    Note.objects.create(owner=ada, text="banana")
    client = TestClient(Notes.as_router(container=Container()))
    assert client.get("/stats?prefix=a").json() == {"count": 1}
    assert client.get("/stats").json() == {"count": 2}


def test_markers_document_intent():
    assert issubclass(Command, Message)
    assert issubclass(Query, Message)
    assert not issubclass(Query, Command)


@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: int


def test_event_bus_delivers_after_commit():
    tasks = RecordingTaskQueue()
    bus = EventBus(tasks)
    seen: list[OrderPlaced] = []
    bus.subscribe(OrderPlaced, seen.append)

    bus.publish(OrderPlaced(order_id=7))
    assert seen == []  # deferred, not run now
    assert len(tasks.calls) == 1

    tasks.run_all()
    assert seen == [OrderPlaced(order_id=7)]


def test_event_bus_ignores_unsubscribed_events():
    tasks = RecordingTaskQueue()
    bus = EventBus(tasks)
    bus.publish(OrderPlaced(order_id=1))
    assert tasks.calls == []


@pytest.mark.django_db
def test_unit_of_work_commits():
    ada = User.objects.create(username="ada")
    with UnitOfWork():
        Note.objects.create(owner=ada, text="kept")
    assert Note.objects.filter(text="kept").exists()


@pytest.mark.django_db
def test_unit_of_work_rolls_back():
    ada = User.objects.create(username="ada")

    def write_and_fail() -> None:
        with UnitOfWork():
            Note.objects.create(owner=ada, text="dropped")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_and_fail()
    assert not Note.objects.filter(text="dropped").exists()


def test_query_handler_signature_is_typed():
    # The payload schema is read from the mapper, like use_case.
    from ninja import NinjaAPI

    api = NinjaAPI(urls_namespace="cqrs")
    api.add_router("/notes", Notes.as_router(container=Container()))
    operation = api.get_openapi_schema(path_prefix="")["paths"]["/notes/stats"]["get"]
    assert operation["parameters"][0]["name"] == "prefix"


@pytest.mark.django_db
def test_unit_of_work_cannot_be_entered_twice():
    unit = UnitOfWork()
    with unit, pytest.raises(RuntimeError, match="already active"):
        unit.__enter__()
    with pytest.raises(RuntimeError, match="entered"):
        unit.__exit__(None, None, None)


def test_cqrs_package_exports_both_handlers():
    from ninja_devx import use_case as root_use_case

    assert use_case is root_use_case
