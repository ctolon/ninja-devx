from typing import Literal

import pytest
from django.core.management import CommandError, call_command
from ninja import Schema

from ninja_devx.crud import CRUDController, ReadOnlyModelController
from ninja_devx.tooling.drift import schema_drift
from tests.testapp.models import Article, Note


class NoteOut(Schema):
    id: int
    text: str
    status: Literal["draft"]
    priority: str
    deleted_at: str  # the model allows NULL


class NoteIn(Schema):
    text: str
    status: Literal["draft", "done", "lost"] = "draft"
    priority: int | None = None
    color: str = "red"


class DriftingNotes(CRUDController[Note, NoteOut, NoteIn]):
    owner_field = "owner"


class ArticleOut(Schema):
    id: int
    title: str
    slug: str


class ArticleIn(Schema):
    title: str


class IncompleteArticles(CRUDController[Article, ArticleOut, ArticleIn]):
    owner_field = "author"


def messages(controller, severity="error"):
    return sorted(
        f"{drift.location}: {drift.message}"
        for drift in schema_drift(controller)
        if drift.severity == severity
    )


def test_output_and_input_drift_is_reported():
    assert messages(DriftingNotes) == [
        "NoteIn.color: Note has no field 'color'",
        "NoteIn.status: accepts ['lost'], not model choices",
        "NoteOut.deleted_at: model DateTimeField is datetime, schema says str",
        "NoteOut.deleted_at: the model allows NULL but the schema does not",
        "NoteOut.priority: model IntegerField is int, schema says str",
        "NoteOut.status: choices ['done'] are rejected",
    ]
    assert messages(DriftingNotes, "warning") == [
        "NoteIn.priority: the schema accepts null, the model does not",
        "NoteIn.text: accepts strings longer than the column (max_length=100)",
    ]
    assert "NoteOut: model field 'archived' is not exposed" in messages(DriftingNotes, "info")


def test_required_fields_missing_from_the_input_schema():
    assert messages(IncompleteArticles) == [
        "ArticleIn: required field 'slug' is not accepted; creating fails"
    ]


def test_matching_schemas_have_no_errors():
    from tests.testapp.api import ArticleController

    assert messages(ArticleController) == []


def test_command_check_uses_mounted_controllers(capsys):
    call_command("devx_scaffold", "--check", "testapp.Article")
    assert "No schema drift" in capsys.readouterr().out


class DriftingReadNotes(ReadOnlyModelController[Note, NoteOut]):
    pass


def test_command_check_fails_on_errors(capsys):
    router = DriftingReadNotes.as_router()  # mounted routers are what the check inspects
    with pytest.raises(CommandError, match="schema drift error"):
        call_command("devx_scaffold", "--check", "testapp.Note")
    assert "DriftingReadNotes  NoteOut.priority  error" in capsys.readouterr().out
    assert router is not None
