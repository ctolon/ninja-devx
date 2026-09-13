from datetime import datetime
from typing import Annotated

from django.http import HttpRequest
from ninja import Schema
from ninja_devx import Controller, get, post
from ninja_devx.crud import CRUDController, Instance
from pydantic import Field

from notes.models import Note


class NoteOut(Schema):
    id: int
    title: str
    body: str
    done: bool
    created: datetime


class NoteIn(Schema):
    title: Annotated[str, Field(max_length=200, min_length=1)]
    body: str = ""


class NoteController(CRUDController[Note, NoteOut, NoteIn]):
    owner_field = "owner"  # authentication, ownership and "owner is me" on create
    scope_queryset_to_owner = True  # lists show only my notes
    search_fields = ("title", "body")
    filter_fields = {"done": ("exact",)}
    ordering_fields = ("created", "title")

    @post("/{pk}/complete", response=NoteOut)
    def complete(self, request: HttpRequest, note: Instance[Note]) -> Note:
        note.done = True
        note.save(update_fields=["done"])
        return note


class HealthController(Controller):
    @get("/", auth=None)
    def health(self, request: HttpRequest) -> dict[str, str]:
        return {"status": "ok"}
