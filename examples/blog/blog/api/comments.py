"""Comments, nested under posts: ``/posts/{post_pk}/comments``."""

from datetime import datetime

from ninja import Schema
from ninja_devx import ControllerOptions, IsAuthenticatedOrReadOnly
from ninja_devx.crud import CRUDController, Parent

from blog.models import Comment, Post


class CommentOut(Schema):
    id: int
    post_id: int
    body: str
    created: datetime


class CommentIn(Schema):
    body: str


class CommentController(CRUDController[Comment, CommentOut, CommentIn]):
    options = ControllerOptions(tags=["comments"], permissions=[IsAuthenticatedOrReadOnly()])
    parent = Parent(Post, field="post")
    ordering_fields = ("created",)
