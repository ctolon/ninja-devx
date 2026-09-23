import json
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

import pytest
from ninja import Schema
from pydantic import Field

from ninja_devx.testing import sample, samples


class AuthorIn(Schema):
    name: str
    age: int


class ArticleIn(Schema):
    title: str
    published: bool = False
    body: str | None = None
    author: AuthorIn


class Colour(Enum):
    RED = "red"
    BLUE = "blue"


class Everything(Schema):
    text: str = "x"
    count: int
    ratio: float
    active: bool
    uid: UUID
    at: datetime
    day: date
    moment: time
    amount: Decimal
    blob: bytes
    tags: list[str]
    mapping: dict[str, int]
    colour: Colour
    kind: Literal["a", "b"]
    author: AuthorIn
    required_note: str | None
    flipped: str | None
    note: str | None = None


def test_sample_uses_types_and_defaults():
    payload = sample(ArticleIn)
    assert payload["title"] == "title"
    assert payload["published"] is False
    assert payload["author"] == {"name": "name", "age": 0}


def test_samples_vary_scalar_values():
    rows = samples(ArticleIn, 3)
    assert [row["title"] for row in rows] == ["title", "title-1", "title-2"]
    assert all(isinstance(row["author"], dict) for row in rows)


def test_samples_require_a_positive_count():
    with pytest.raises(ValueError, match="positive"):
        samples(ArticleIn, 0)


def test_sample_of_empty_schema_is_empty():
    class Empty(Schema):
        pass

    assert sample(Empty) == {}


def test_sample_covers_every_supported_type_and_validates():
    payload = sample(Everything)
    assert payload["count"] == 0
    assert payload["ratio"] == 0.0
    assert payload["active"] is True
    assert payload["tags"] == []
    assert payload["mapping"] == {}
    assert payload["colour"] == "red"
    assert payload["kind"] == "a"
    assert payload["author"] == {"name": "name", "age": 0}
    assert payload["required_note"] == "required_note"
    assert payload["flipped"] == "flipped"
    assert "note" not in payload
    assert Everything.model_validate(payload)


def test_sample_is_json_serialisable():
    payload = sample(Everything)
    del payload["blob"]
    assert json.loads(json.dumps(payload))["uid"] == payload["uid"]


def test_sample_prefers_examples_over_derived_values():
    class Example(Schema):
        title: str = Field(default="d", examples=["e"])
        raw: object = None

    assert sample(Example) == {"title": "d"}

    class RequiredExample(Schema):
        title: str = Field(examples=["e"])

    assert sample(RequiredExample) == {"title": "e"}


def test_sample_honours_integer_lower_bounds():
    class Order(Schema):
        quantity: int = Field(ge=1)
        priority: int = Field(gt=5)

    rows = samples(Order, 2)
    assert [row["quantity"] for row in rows] == [1, 1]
    assert [row["priority"] for row in rows] == [6, 6]
    assert all(Order.model_validate(row) for row in rows)


def test_sample_rejects_types_without_a_rule():
    class Odd(Schema):
        raw: object

    with pytest.raises(TypeError, match=r"Odd\.raw"):
        sample(Odd)
