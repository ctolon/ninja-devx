"""CSV and JSON Lines export and import for model controllers.

::

    class ContactController(ExportMixin[Contact, ContactOut], ImportMixin[Contact, ContactIn],
                            CRUDController[Contact, ContactOut, ContactIn]):
        export_formats = ("csv", "jsonl")

- ``GET /export?format=csv`` streams every object the list operation would return
  (same filters, ordering, scoping and field visibility), without pagination.
- ``POST /import`` takes a CSV or JSONL upload, validates every row with the input
  schema and creates the objects through ``perform_create`` in one transaction: either
  all rows are imported or none (``?dry_run=true`` only validates). Errors are 422 with
  ``loc: ["file", <row>, <field>]``.
"""

from __future__ import annotations

import builtins
import csv
import io
import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from itertools import islice
from types import MappingProxyType
from typing import IO, ClassVar, Final, Generic, Literal, TypeAlias, cast

from asgiref.sync import sync_to_async
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Model, QuerySet
from django.http import HttpRequest, StreamingHttpResponse
from django.utils.translation import gettext as _
from ninja import File, FilterSchema, Query, Schema, Status, UploadedFile
from ninja.errors import HttpError, ValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ..layers.errors import ValidationFailed
from ..routing.operations import async_variant, get, post
from .annotations import Filters, Ordering, OrderingSchema
from .controllers import CreateHooks, InT, ListConfig, ModelT, OutT
from .writes import write_scope

__all__ = ["ExportFormat", "ExportMixin", "ImportMixin", "ImportResult"]

ExportFormat = Literal["csv", "jsonl"]
_MEDIA_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {"csv": "text/csv", "jsonl": "application/x-ndjson"}
)
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
_CHUNK: Final = 500
_Encoder: TypeAlias = Callable[[object], str]


class ExportQuery(Schema):
    format: ExportFormat = "csv"


class ImportResult(Schema):
    created: int
    dry_run: bool


class ImportOptions(Schema):
    dry_run: bool = False


def _flatten(value: object, prefix: str, row: dict[str, object]) -> None:
    if isinstance(value, Mapping):
        for key, item in cast("Mapping[str, object]", value).items():
            _flatten(item, f"{prefix}.{key}" if prefix else str(key), row)
    else:
        row[prefix] = value


def _cell(value: object, escape: bool) -> object:
    if isinstance(value, list | dict):
        return json.dumps(value, cls=DjangoJSONEncoder)
    if escape and isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value  # spreadsheet formula injection (OWASP)
    return value


class _CSVLines:
    """Encode flattened rows as CSV lines; the header comes from the first row."""

    def __init__(self, escape: bool) -> None:
        self.escape = escape
        self.header: list[str] | None = None

    def encode(self, data: object) -> str:
        row: dict[str, object] = {}
        _flatten(data, "", row)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        if self.header is None:
            self.header = list(row)
            writer.writerow(self.header)
        writer.writerow([_cell(row.get(name), self.escape) for name in self.header])
        return buffer.getvalue()


class ExportMixin(ListConfig[ModelT, OutT], Generic[ModelT, OutT]):
    """``GET /export``: the filtered list as CSV or JSON Lines, streamed."""

    export_formats: ClassVar[Sequence[ExportFormat]] = ("csv", "jsonl")
    """Formats clients may ask for; the first is the default."""
    export_filename: ClassVar[str | None] = None
    """Download name without extension (default: the model's plural name)."""
    csv_escape_formulas: ClassVar[bool] = True
    """Prefix text cells starting with ``= + - @`` with ``'`` so spreadsheets don't run them."""

    @get(
        "/export",
        response={200: None},
        summary="Export as CSV or JSON Lines",
        openapi_extra={
            "responses": {
                "200": {
                    "description": "The exported rows",
                    "content": {
                        "text/csv": {"schema": {"type": "string"}},
                        "application/x-ndjson": {"schema": {"type": "string"}},
                    },
                }
            }
        },
    )
    def export(
        self,
        request: HttpRequest,
        filters: Filters,
        ordering: Ordering,
        query: Query[ExportQuery],
    ) -> StreamingHttpResponse:
        rows = self._rows(request, filters, ordering, query.format)
        lines = self._encode(request, rows, query.format)
        return self._response(lines, query.format)

    @async_variant(export)
    async def aexport(
        self,
        request: HttpRequest,
        filters: Filters,
        ordering: Ordering,
        query: Query[ExportQuery],
    ) -> StreamingHttpResponse:
        await self.aprepare_request(request)
        rows = self._rows(request, filters, ordering, query.format)

        async def lines() -> AsyncIterator[str]:
            iterator = iter(rows)
            take = sync_to_async(_take, thread_sensitive=True)
            encoder = self._encoder(query.format)
            while chunk := await take(iterator, request, type(self).output_schema(), _CHUNK):
                for item in chunk:
                    yield encoder(item)

        return self._response(lines(), query.format)

    # --- helpers ------------------------------------------------------------------------

    def _rows(
        self, request: HttpRequest, filters: FilterSchema, ordering: OrderingSchema, format: str
    ) -> Iterator[Model] | builtins.list[Model]:
        if format not in type(self).export_formats:
            raise HttpError(
                422,
                _("Unsupported format %(format)s; use %(formats)s")
                % {"format": format, "formats": ", ".join(type(self).export_formats)},
            )
        result = self.list_queryset(request, filters, ordering)
        if isinstance(result, QuerySet):
            return cast("QuerySet[Model]", result).iterator(chunk_size=2000)
        return cast("builtins.list[Model]", result)

    def _encoder(self, format: str) -> _Encoder:
        if format == "csv":
            return _CSVLines(type(self).csv_escape_formulas).encode
        return _jsonl

    def _encode(
        self, request: HttpRequest, rows: Iterator[Model] | builtins.list[Model], format: str
    ) -> Iterator[str]:
        encoder = self._encoder(format)
        for obj in rows:
            yield encoder(_dump(type(self).output_schema(), request, obj))

    def _response(
        self, lines: Iterator[str] | AsyncIterator[str], format: str
    ) -> StreamingHttpResponse:
        cls = type(self)
        name = cls.export_filename or str(cls.get_model()._meta.verbose_name_plural).replace(
            " ", "_"
        )
        response = StreamingHttpResponse(
            lines, content_type=f"{_MEDIA_TYPES[format]}; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{name}.{format}"'
        return response


def _jsonl(data: object) -> str:
    return json.dumps(data, cls=DjangoJSONEncoder, separators=(",", ":")) + "\n"


def _dump(schema: type[BaseModel] | None, request: HttpRequest, obj: Model) -> object:
    if schema is None:
        raise HttpError(500, "The controller has no output schema")
    return schema.model_validate(obj, context={"request": request}).model_dump(
        mode="json", context={"request": request}, by_alias=True
    )


def _take(
    iterator: Iterator[Model], request: HttpRequest, schema: type[BaseModel] | None, size: int
) -> builtins.list[object]:
    return [_dump(schema, request, obj) for obj in islice(iterator, size)]


class ImportMixin(CreateHooks[ModelT, InT], Generic[ModelT, InT]):
    """``POST /import``: create objects from a CSV or JSON Lines file, all or nothing."""

    max_import_bytes: ClassVar[int] = 10_000_000
    """Reject files larger than this byte limit before parsing or starting writes."""
    max_import_rows: ClassVar[int] = 10_000
    """Larger files are rejected with 413."""
    max_import_errors: ClassVar[int] = 50
    """Stop validating after this many errors."""

    @post(
        "/import",
        response={200: ImportResult, 201: ImportResult},
        summary="Import from CSV or JSON Lines",
    )
    def import_rows(
        self, request: HttpRequest, file: File[UploadedFile], options: Query[ImportOptions]
    ) -> Status[ImportResult]:
        return self._import(request, file, options.dry_run)

    @async_variant(import_rows)
    async def aimport_rows(
        self, request: HttpRequest, file: File[UploadedFile], options: Query[ImportOptions]
    ) -> Status[ImportResult]:
        await self.aprepare_request(request)
        return await self.run_sync(self._import, request, file, options.dry_run)

    def _import(
        self, request: HttpRequest, file: UploadedFile, dry_run: bool
    ) -> Status[ImportResult]:
        schema = type(self).input_schema()
        if schema is None:
            raise HttpError(500, "The controller has no input schema")
        if file.size is None or file.size > type(self).max_import_bytes:
            raise HttpError(413, f"Import files are limited to {type(self).max_import_bytes} bytes")
        rows = list(islice(_parse(file), type(self).max_import_rows + 1))
        if len(rows) > type(self).max_import_rows:
            raise HttpError(
                413,
                _("At most %(count)d rows can be imported at once")
                % {"count": type(self).max_import_rows},
            )
        errors: list[dict[str, object]] = []
        created = 0
        with write_scope(self, request):
            for number, row in rows:
                try:
                    payload = schema.model_validate(row)
                    self.refresh(request, self.perform_create(request, cast("InT", payload)))
                    created += 1
                except PydanticValidationError as exc:
                    errors.extend(
                        {
                            "type": error["type"],
                            "loc": ["file", number, *error["loc"]],
                            "msg": error["msg"],
                        }
                        for error in exc.errors(include_url=False)
                    )
                except ValidationFailed as exc:
                    errors.extend(
                        {
                            "type": exc.code,
                            "loc": ["file", number, *([field] if field else [])],
                            "msg": message,
                        }
                        for field, messages in exc.errors.items()
                        for message in messages
                    )
                if len(errors) >= type(self).max_import_errors:
                    break
            if errors or dry_run:
                transaction.set_rollback(True, using=self.write_database(request))
        if errors:
            raise ValidationError(errors[: type(self).max_import_errors])
        return Status(200 if dry_run else 201, ImportResult(created=created, dry_run=dry_run))


def _parse(file: UploadedFile) -> Iterator[tuple[int, dict[str, object]]]:
    try:
        yield from _parse_records(file)
    except (UnicodeError, csv.Error) as exc:
        raise ValidationError(
            [{"type": "file_invalid", "loc": ["file"], "msg": "Expected valid UTF-8 CSV or JSONL"}]
        ) from exc


def _parse_records(file: UploadedFile) -> Iterator[tuple[int, dict[str, object]]]:
    name = (file.name or "").lower()
    content_type = (file.content_type or "").lower()
    stream: object = getattr(file, "file")  # noqa: B009 - untyped in django-stubs
    text = io.TextIOWrapper(cast("IO[bytes]", stream), encoding="utf-8-sig", newline="")
    if name.endswith((".jsonl", ".ndjson")) or "json" in content_type:
        for number, line in enumerate(text, start=1):
            if not line.strip():
                continue
            try:
                value: object = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    [{"type": "json_invalid", "loc": ["file", number], "msg": str(exc)}]
                ) from exc
            if not isinstance(value, dict):
                raise ValidationError(
                    [
                        {
                            "type": "dict_type",
                            "loc": ["file", number],
                            "msg": _("Each line must be a JSON object"),
                        }
                    ]
                )
            yield number, cast("dict[str, object]", value)
        return
    reader = csv.DictReader(text)
    for index, record in enumerate(reader, start=2):  # row 1 is the header
        yield (
            index,
            {key: _csv_value(value) for key, value in record.items() if key and value != ""},
        )


def _csv_value(value: str | None) -> object:
    if value and value[0] in "[{":
        try:
            return cast("object", json.loads(value))
        except json.JSONDecodeError:
            return value
    return value
