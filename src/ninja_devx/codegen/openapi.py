"""A small, typed view of the OpenAPI documents Django Ninja produces."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from django.utils.module_loading import import_string
from ninja import NinjaAPI

__all__ = [
    "Operation",
    "Parameter",
    "Schema",
    "as_list",
    "as_mapping",
    "as_strings",
    "load_api",
    "read_operations",
    "ref_name",
]

Schema: TypeAlias = Mapping[str, object]
Location = Literal["path", "query", "header", "cookie"]
_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    location: Location
    required: bool
    schema: Schema


@dataclass(frozen=True, slots=True)
class ResponseVariant:
    status: str
    media_type: str | None
    schema: Schema


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    method: str
    path: str
    summary: str
    parameters: tuple[Parameter, ...]
    body: Schema | None
    body_required: bool
    response: Schema | None
    """Union of all documented 2xx JSON, text and binary representations."""
    has_body: bool
    """Whether any documented success response has a body."""
    response_variants: tuple[ResponseVariant, ...] = ()
    body_media_type: str = "application/json"
    errors: tuple[ResponseVariant, ...] = ()
    """Documented non-2xx responses that carry a body."""
    streaming: bool = False
    """A 2xx ``text/event-stream`` response, generated as an event iterator."""


def load_api(path: str) -> NinjaAPI:
    """Import a ``NinjaAPI`` from a dotted path, e.g. ``config.urls.api``."""
    api: object = import_string(path)
    if not isinstance(api, NinjaAPI):
        raise TypeError(f"{path} is not a NinjaAPI instance")
    return api


def ref_name(reference: str) -> str:
    return reference.rsplit("/", 1)[-1]


def named_multipart_bodies(document: Schema) -> Schema:
    """Give inline multipart objects stable model names without mutating the input."""
    components = dict(_mapping(document.get("components")))
    schemas = dict(_mapping(components.get("schemas")))
    paths: dict[str, object] = {}
    counter = 0
    for path, raw_path in _mapping(document.get("paths")).items():
        path_item = dict(_mapping(raw_path))
        for method in _METHODS:
            if method not in path_item:
                continue
            operation = dict(_mapping(path_item[method]))
            body = dict(_resolve(document, operation.get("requestBody"), "requestBodies"))
            content = dict(_mapping(body.get("content")))
            part = dict(_mapping(content.get("multipart/form-data")))
            schema = _mapping(part.get("schema"))
            if schema.get("type") != "object" or not schema.get("properties"):
                continue
            while f"MultipartBody{counter}" in schemas:
                counter += 1
            name = f"MultipartBody{counter}"
            counter += 1
            schemas[name] = schema
            part["schema"] = {"$ref": f"#/components/schemas/{name}"}
            content["multipart/form-data"] = part
            body["content"] = content
            operation["requestBody"] = body
            path_item[method] = operation
        paths[path] = path_item
    components["schemas"] = schemas
    return {**document, "paths": paths, "components": components}


def read_operations(document: Schema) -> list[Operation]:
    operations: list[Operation] = []
    for path, item in _mapping(document.get("paths")).items():
        path_item = _mapping(item)
        for method, raw in path_item.items():
            if method not in _METHODS:
                continue
            operation = _mapping(raw)
            operation_id = str(operation.get("operationId") or _fallback_id(method, path))
            parameters: dict[tuple[Location, str], Parameter] = {}
            for raw_parameter in [
                *_sequence(path_item.get("parameters")),
                *_sequence(operation.get("parameters")),
            ]:
                parameter = _resolve(document, raw_parameter, "parameters")
                location = _location(parameter.get("in"))
                name = str(parameter["name"])
                expected_style = "form" if location in ("query", "cookie") else "simple"
                if parameter.get("style", expected_style) != expected_style:
                    raise ValueError(f"{operation_id}: unsupported parameter style for {name!r}")
                if location == "query" and parameter.get("explode", True) is not True:
                    raise ValueError(
                        f"{operation_id}: query explode=False is unsupported for {name!r}"
                    )
                if parameter.get("allowReserved"):
                    raise ValueError(f"{operation_id}: allowReserved is unsupported for {name!r}")
                schema = _mapping(parameter.get("schema"))
                if parameter.get("content") or schema.get("type") == "object":
                    raise ValueError(
                        f"{operation_id}: structured parameter {name!r} is unsupported"
                    )
                parameters[location, name] = Parameter(
                    name=name,
                    location=location,
                    required=bool(parameter.get("required", False)),
                    schema=schema,
                )
            body = _resolve(document, operation.get("requestBody"), "requestBodies")
            body_content = _mapping(body.get("content"))
            body_schema = _json_schema(body_content) if body else None
            body_media_type = "application/json"
            if body and body_schema is None and "multipart/form-data" in body_content:
                part = _mapping(body_content["multipart/form-data"])
                if part.get("encoding"):
                    raise ValueError(f"{operation_id}: custom multipart encoding is unsupported")
                body_schema = _mapping(part.get("schema"))
                resolved = _resolve(document, body_schema, "schemas")
                if resolved.get("type") != "object":
                    raise ValueError(f"{operation_id}: multipart body must be an object")
                for field_name, value in _mapping(resolved.get("properties")).items():
                    field = _mapping(value)
                    if field.get("type") not in {"string", "integer", "number", "boolean"}:
                        raise ValueError(
                            f"{operation_id}: unsupported multipart field {field_name!r}"
                        )
                body_media_type = "multipart/form-data"
            if body and body_schema is None:
                raise ValueError(
                    f"{operation_id}: unsupported request media types {sorted(body_content)}; "
                    "JSON or simple multipart/form-data is required"
                )
            responses = {
                code: _resolve(document, value, "responses")
                for code, value in _mapping(operation.get("responses")).items()
            }
            response, has_body, variants, streaming = _success_schema(responses, operation_id)
            errors = _error_variants(responses)
            operations.append(
                Operation(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    summary=str(operation.get("summary", "")),
                    parameters=tuple(parameters.values()),
                    body=body_schema,
                    body_required=bool(body.get("required", False)),
                    response=response,
                    has_body=has_body,
                    response_variants=variants,
                    body_media_type=body_media_type,
                    errors=errors,
                    streaming=streaming,
                )
            )
    return operations


def _resolve(document: Schema, value: object, section: str) -> Schema:
    mapping = _mapping(value)
    seen: set[str] = set()
    while "$ref" in mapping:
        reference = str(mapping["$ref"])
        prefix = f"#/components/{section}/"
        if not reference.startswith(prefix) or reference in seen:
            raise ValueError(f"Unsupported or circular {section} reference: {reference!r}")
        seen.add(reference)
        name = reference.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
        entries = _mapping(_mapping(document.get("components")).get(section))
        if name not in entries:
            raise ValueError(f"Unknown {section} reference: {reference!r}")
        mapping = _mapping(entries[name])
    return mapping


def _success_schema(
    responses: Schema, operation_id: str
) -> tuple[Schema | None, bool, tuple[ResponseVariant, ...], bool]:
    schemas: list[Schema] = []
    variants: list[ResponseVariant] = []
    has_body = False
    streaming = False
    for code, raw in sorted(responses.items()):
        if not code.startswith("2"):
            continue
        content = _mapping(_mapping(raw).get("content"))
        if code == "204":
            empty = {"type": "null", "x-empty-response": True}
            schemas.append(empty)
            variants.append(ResponseVariant(code, None, empty))
            continue
        has_body = True
        if not content:
            # Ninja uses a description-only 200 for an unannotated response. Its
            # body is unknown, not necessarily empty.
            schemas.append({})
            variants.append(ResponseVariant(code, None, {}))
            continue
        for media_type, representation in content.items():
            media_type = media_type.split(";", 1)[0].lower()
            schema: Schema
            if media_type == "text/event-stream":
                schema = {"type": "string", "x-event-stream": True}
                streaming = True
            elif _is_json(media_type):
                schema = _mapping(_mapping(representation).get("schema"))
            elif media_type.startswith("text/"):
                schema = {"type": "string"}
            else:
                schema = {"type": "string", "x-binary-response": True}
            variants.append(ResponseVariant(code, media_type, schema))
            if schema not in schemas:
                schemas.append(schema)
    if not has_body:
        return None, False, tuple(variants), streaming
    if streaming:
        return None, False, tuple(variants), True
    return (schemas[0] if len(schemas) == 1 else {"anyOf": schemas}), True, tuple(variants), False


def _error_variants(responses: Schema) -> tuple[ResponseVariant, ...]:
    """Documented non-2xx JSON responses, so generated clients can type error bodies."""
    found: list[ResponseVariant] = []
    for code, raw in sorted(responses.items()):
        if code.startswith("2"):
            continue
        for media_type, representation in _mapping(_mapping(raw).get("content")).items():
            media_type = media_type.split(";", 1)[0].lower()
            if _is_json(media_type):
                error_schema = _mapping(_mapping(representation).get("schema"))
                found.append(ResponseVariant(code, media_type, error_schema))
    return tuple(found)


def _is_json(media_type: str) -> bool:
    media_type = media_type.split(";", 1)[0].lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _json_schema(content: Schema) -> Schema | None:
    for media_type, value in content.items():
        if _is_json(media_type):
            return _mapping(_mapping(value).get("schema"))
    return None


def _location(value: object) -> Location:
    if value in ("path", "query", "header", "cookie"):
        return value
    raise ValueError(f"Unsupported parameter location {value!r}")


def _fallback_id(method: str, path: str) -> str:
    return f"{method}_{re.sub(r'[^0-9a-zA-Z]+', '_', path).strip('_')}"


def as_mapping(value: object) -> Schema:
    """``value`` as a string-keyed mapping, or an empty one."""
    if not isinstance(value, Mapping):
        return {}
    items = cast("Mapping[object, object]", value).items()
    return {str(key): item for key, item in items}


def as_list(value: object) -> list[object]:
    return list(cast("Sequence[object]", value)) if isinstance(value, list | tuple) else []


def as_strings(value: object) -> list[str]:
    return [str(item) for item in as_list(value)]


_mapping = as_mapping
_sequence = as_list
