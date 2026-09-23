from datetime import datetime

import pytest

from ninja_devx.crud import SoftDelete, SoftDeleteMixin, soft_delete_unique
from ninja_devx.exceptions import ControllerConfigError
from tests.testapp.models import Note


class FakeRelated:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def all(self) -> "FakeRelated":
        return self

    def update(self, **values: object) -> int:
        self.updates.append(values)
        return 1


class FakeInstance:
    def __init__(self) -> None:
        self.comments = FakeRelated()


class FakeController:
    soft_delete_cascade = ("comments",)


def test_cascade_marks_related_objects_deleted_and_restored(db):
    resolved = SoftDelete("deleted_at").resolved(Note, "NoteController")
    instance = FakeInstance()
    SoftDeleteMixin._cascade(FakeController(), instance, resolved, deleted=True)  # type: ignore[arg-type]
    assert isinstance(instance.comments.updates[0]["deleted_at"], datetime)
    SoftDeleteMixin._cascade(FakeController(), instance, resolved, deleted=False)  # type: ignore[arg-type]
    assert instance.comments.updates[1] == {"deleted_at": None}


def test_soft_delete_unique_builds_a_partial_constraint(db):
    constraint = soft_delete_unique(Note, "text")
    assert constraint.name == "uniq_active_testapp_note_text"
    assert constraint.condition is not None
    assert constraint.fields == ("text",)


def test_soft_delete_unique_accepts_a_name(db):
    constraint = soft_delete_unique(Note, "text", name="note_active_text")
    assert constraint.name == "note_active_text"


def test_soft_delete_unique_needs_fields(db):
    with pytest.raises(ControllerConfigError, match="at least one field"):
        soft_delete_unique(Note)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"deleted_by": "text"}, "nullable ForeignKey"),
        ({"deleted_at": "text"}, "nullable DateTimeField"),
    ],
)
def test_soft_delete_validates_field_types(db, options, message):
    with pytest.raises(ControllerConfigError, match=message):
        SoftDelete("deleted_at", **options).resolved(Note, "Note")
