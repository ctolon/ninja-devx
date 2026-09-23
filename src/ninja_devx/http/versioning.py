"""Serve an older response shape, negotiated by a request header.

Declare the schemas a controller used to return, keyed by the version number clients
asked for; the current shape is whatever the operation already declares with ``response=``::

    class PostOutV1(Schema):
        id: int
        title: str

    class PostController(VersionedResponseMixin, CRUDController[Post, PostOut, PostIn]):
        response_versions = {1: PostOutV1}

A client sending ``Accept-Version: 1`` gets ``PostOutV1`` (and ``list[PostOutV1]`` for list
operations), re-validated from the very same rendered response, so there is no second
database round trip. Omitting the header serves the latest shape. Every response carries
``X-API-Version`` naming the version actually served; an unknown ``Accept-Version`` answers
406 in the project's error format.

Only single-schema and ``list[...]`` responses can be downgraded; other shapes (status-keyed
responses without one 2xx entry, ``dict`` bodies...) still get the header and 406 handling,
but are served unchanged since there is no schema to validate them against.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Final, cast, get_args, get_origin

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    JsonResponse,
    StreamingHttpResponse,
)
from ninja.responses import NinjaJSONEncoder
from pydantic import BaseModel, ValidationError

from .._internal.types import JSONValue
from ..routing.controller import Controller, ControllerOptions
from ..routing.hooks import get_operation
from ..routing.operations import OperationSpec
from .errors import render
from .middleware import Middleware

__all__ = ["VersionedResponseMiddleware", "VersionedResponseMixin"]

_VERSION_ATTR: Final = "_ninja_devx_response_version"


@dataclass(frozen=True, slots=True)
class _VersionedOperation:
    """Per-operation metadata read back by ``VersionedResponseMiddleware``."""

    many: bool
    versions: Mapping[int, type[BaseModel]]


class VersionedResponseMiddleware(Middleware):
    """Negotiate ``Accept-Version`` and downgrade the rendered response.

    Added automatically by ``VersionedResponseMixin`` when ``response_versions`` is set;
    construct it directly only to use it without the mixin.

    :param response_versions: ``{version: schema}`` for every version but the latest.
    :param header: Request header naming the wanted version.
    :param response_header: Response header naming the version actually served.
    """

    def __init__(
        self,
        response_versions: Mapping[int, type[BaseModel]],
        *,
        header: str = "Accept-Version",
        response_header: str = "X-API-Version",
    ) -> None:
        self.versions: Mapping[int, type[BaseModel]] = dict(response_versions)
        self.current: int = max(self.versions, default=0) + 1
        self.header = header
        self.response_header = response_header

    def process_request(self, request: HttpRequest) -> HttpResponseBase | None:
        raw = request.headers.get(self.header)
        if raw is None:
            request.__dict__[_VERSION_ATTR] = self.current
            return None
        try:
            version = int(raw)
        except ValueError:
            return self._rejected(raw)
        if version != self.current and version not in self.versions:
            return self._rejected(raw)
        request.__dict__[_VERSION_ATTR] = version
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        version: int | None = request.__dict__.get(_VERSION_ATTR)
        if version is None:
            return response
        if version != self.current:
            operation = get_operation(request)
            info = operation.meta(_VersionedOperation) if operation is not None else None
            if info is not None and version in info.versions:
                response = _reshape(info.versions[version], response, many=info.many)
        if not response.has_header(self.response_header):
            response[self.response_header] = str(version)
        return response

    def _rejected(self, raw: str) -> HttpResponseBase:
        supported = ", ".join(str(value) for value in (*sorted(self.versions), self.current))
        return render(
            406,
            "unsupported_api_version",
            {
                "detail": f"Unsupported {self.header} {raw!r}; supported versions: {supported}.",
                "code": "unsupported_api_version",
            },
        )


def _reshape(
    schema: type[BaseModel], response: HttpResponseBase, *, many: bool
) -> HttpResponseBase:
    if isinstance(response, StreamingHttpResponse) or not (200 <= response.status_code < 300):
        return response
    try:
        payload: object = json.loads(cast("HttpResponse", response).content)
    except ValueError:
        return response
    try:
        if many:
            if not isinstance(payload, list):
                return response
            items = cast("list[object]", payload)
            data: object = [schema.model_validate(item).model_dump(mode="json") for item in items]
        else:
            data = schema.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        return response
    downgraded = JsonResponse(
        data, encoder=NinjaJSONEncoder, safe=isinstance(data, dict), status=response.status_code
    )
    for name in ("Vary", "Cache-Control"):
        if response.has_header(name):
            downgraded[name] = response[name]
    return downgraded


def _success_schema(response: object) -> object:
    if not isinstance(response, Mapping):
        return response
    mapping = cast("Mapping[object, object]", response)
    candidates = [
        value
        for key, value in mapping.items()
        for code in (cast("frozenset[object]", key) if isinstance(key, frozenset) else {key})
        if isinstance(code, int) and 200 <= code < 300
    ]
    return candidates[0] if len(candidates) == 1 else None


def _unwrap_response(response: object) -> tuple[type[BaseModel] | None, bool]:
    """The schema an operation's ``response=`` serializes, and whether it is a list of it."""
    schema = _success_schema(response)
    if get_origin(schema) is list:
        args = get_args(schema)
        item = args[0] if args else None
        if isinstance(item, type) and issubclass(item, BaseModel):
            return item, True
        return None, False
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema, False
    return None, False


def _document_header(
    spec: OperationSpec, header: str, versions: Mapping[int, type[BaseModel]]
) -> OperationSpec:
    extra = dict(spec.options.get("openapi_extra") or {})
    parameters = list(cast("list[JSONValue]", extra.get("parameters") or []))
    parameter: dict[str, JSONValue] = {
        "name": header,
        "in": "header",
        "required": False,
        "schema": {"type": "integer"},
        "description": f"Serve an older response shape ({sorted(versions)}); omit for the latest.",
    }
    parameters.append(parameter)
    extra["parameters"] = parameters
    return spec.with_options(openapi_extra=extra)


class VersionedResponseMixin(Controller):
    """Adds ``Accept-Version`` negotiation to every operation of a controller.

    List it first so its ``merged_options``/``customize_operation`` see the final
    ``response=``: ``class PostController(VersionedResponseMixin, CRUDController[...])``.
    """

    response_versions: ClassVar[Mapping[int, type[BaseModel]]] = MappingProxyType({})
    """Older response schemas, keyed by the version clients ask for with ``Accept-Version``.
    The latest version is implicitly one more than the highest key here."""
    response_version_header: ClassVar[str] = "Accept-Version"
    """Request header naming the wanted version."""
    response_version_response_header: ClassVar[str] = "X-API-Version"
    """Response header naming the version actually served."""

    @classmethod
    def merged_options(cls, overrides: ControllerOptions | None = None) -> ControllerOptions:
        merged = super().merged_options(overrides)
        if cls.response_versions:
            existing = merged.get("middleware", ())
            if not any(isinstance(item, VersionedResponseMiddleware) for item in existing):
                merged["middleware"] = (
                    VersionedResponseMiddleware(
                        cls.response_versions,
                        header=cls.response_version_header,
                        response_header=cls.response_version_response_header,
                    ),
                    *existing,
                )
        return merged

    @classmethod
    def customize_operation(cls, name: str, spec: OperationSpec) -> OperationSpec:
        spec = super().customize_operation(name, spec)
        versions = cls.response_versions
        if not versions:
            return spec
        schema, many = _unwrap_response(spec.options.get("response"))
        if schema is not None:
            info = _VersionedOperation(many=many, versions=MappingProxyType(dict(versions)))
            spec = spec.with_options(meta=(*spec.options.get("meta", ()), info))
        return _document_header(spec, cls.response_version_header, versions)

    @classmethod
    def documented_errors(cls, name: str, spec: OperationSpec) -> frozenset[int]:
        errors = super().documented_errors(name, spec)
        if cls.response_versions:
            errors |= {406}
        return errors
