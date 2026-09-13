from datetime import datetime
from typing import Annotated, Literal

from ninja import Schema
from ninja_devx import FieldVisibility, VisibleTo
from pydantic import Field

from tracker.tenancy import IsWorkspaceAdmin


class ProjectOut(Schema):
    id: int
    name: str
    updated_at: datetime


class ProjectIn(Schema):
    name: Annotated[str, Field(max_length=100, min_length=1)]


class IssueOut(FieldVisibility, Schema):
    id: int
    title: str
    status: Literal["open", "closed"]
    priority: int
    created: datetime
    internal_notes: Annotated[str | None, VisibleTo(IsWorkspaceAdmin(), hidden="omit")] = None


class IssueIn(Schema):
    title: Annotated[str, Field(max_length=200, min_length=1)]
    status: Literal["open", "closed"] = "open"
    priority: Annotated[int, Field(ge=0, le=5)] = 3
    internal_notes: str = ""


class DocumentOut(Schema):
    id: int
    title: str
    body: str
    owner_id: int
    updated_at: datetime


class DocumentIn(Schema):
    title: Annotated[str, Field(max_length=200, min_length=1)]
    body: str = ""
