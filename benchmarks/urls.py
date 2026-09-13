"""The same endpoints as plain Ninja functions and as controllers, sync and async."""

from django.contrib.auth.models import User
from django.urls import path
from ninja import NinjaAPI, Router, Schema

from ninja_devx import Container, Controller, get
from ninja_devx.crud import AsyncReadOnlyModelController, ReadOnlyModelController
from tests.testapp.models import Note


class Item(Schema):
    id: int
    name: str


class NoteOut(Schema):
    id: int
    text: str
    owner_id: int


functions = Router()


@functions.get("/items/{item_id}", response=Item)
def item(request, item_id: int, q: str = ""):
    return {"id": item_id, "name": q}


@functions.get("/aitems/{item_id}", response=Item)
async def aitem(request, item_id: int, q: str = ""):
    return {"id": item_id, "name": q}


@functions.get("/notes", response=list[NoteOut])
def notes(request):
    return list(Note.objects.all())


@functions.get("/anotes", response=list[NoteOut])
async def anotes(request):
    return [note async for note in Note.objects.all()]


class Service:
    pass


class Items(Controller):
    def __init__(self, service: Service) -> None:
        self.service = service

    @get("/items/{item_id}", response=Item)
    def item(self, request, item_id: int, q: str = ""):
        return {"id": item_id, "name": q}

    @get("/aitems/{item_id}", response=Item)
    async def aitem(self, request, item_id: int, q: str = ""):
        return {"id": item_id, "name": q}


class Notes(ReadOnlyModelController[Note, NoteOut]):
    pass


class AsyncNotes(AsyncReadOnlyModelController[Note, NoteOut]):
    pass


container = Container()
api = NinjaAPI(urls_namespace="bench")
api.add_router("/fn", functions)
api.add_router("/ctl", Items.as_router(container=container))
api.add_router("/ctl/notes", Notes.as_router())
api.add_router("/ctl/anotes", AsyncNotes.as_router())

urlpatterns = [path("api/", api.urls)]


def seed(count: int = 20) -> None:
    """``count`` notes: every list endpoint returns all of them."""
    owner, _ = User.objects.get_or_create(username="bench")
    missing = count - Note.objects.count()
    Note.objects.bulk_create(Note(owner=owner, text=f"note {i}") for i in range(max(missing, 0)))
