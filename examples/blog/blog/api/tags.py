"""Tags: read-only, public."""

from ninja import Schema
from ninja_devx.crud import ReadOnlyModelController

from blog.models import Tag


class TagOut(Schema):
    id: int
    name: str


class TagController(ReadOnlyModelController[Tag, TagOut]):
    search_fields = ("name",)
    ordering_fields = ("name",)
