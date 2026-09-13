"""Self-service key management for the authenticated user."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar

from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import Schema, Status
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate
from pydantic import Field

from ...routing.controller import Controller, ControllerOptions
from ...routing.hooks import OperationInfo
from ...routing.operations import delete, get, post
from ...security.auth import request_user
from ...security.permissions import IsAuthenticated
from .auth import create_api_key, current_api_key, revoke_api_key
from .models import APIKey

__all__ = ["APIKeyController", "APIKeyCreated", "APIKeyIn", "APIKeyOut"]


class APIKeyOut(Schema):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    rate_limit: str
    created: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class APIKeyIn(Schema):
    name: str = Field(max_length=100, min_length=1)
    scopes: list[str] = Field(default_factory=list[str])
    expires_at: datetime | None = None


class APIKeyCreated(APIKeyOut):
    key: str = Field(description="The raw key. It is shown once and never again.")


class APIKeyController(Controller):
    """``GET /`` my keys, ``POST /`` create one (raw key in the response), ``DELETE /{id}``."""

    options = ControllerOptions(permissions=[IsAuthenticated()], tags=["api keys"])
    grantable_scopes: ClassVar[Sequence[str] | None] = None
    """Scopes users may put on their keys (``None``: any)."""

    def before_operation(self, request: HttpRequest, operation: OperationInfo) -> None:
        super().before_operation(request, operation)
        if current_api_key(request) is not None:
            raise HttpError(403, "API keys cannot manage credentials; use user authentication")

    @get(
        "/",
        response=list[APIKeyOut],
        decorators=[paginate(PageNumberPagination, page_size=50, max_page_size=100)],
    )
    def list_keys(self, request: HttpRequest) -> QuerySet[APIKey]:
        return APIKey.objects.filter(user=request_user(request)).order_by("-created", "-pk")

    @post("/", response={201: APIKeyCreated})
    def create_key(self, request: HttpRequest, payload: APIKeyIn) -> Status[dict[str, object]]:
        allowed = type(self).grantable_scopes
        if allowed is not None and (unknown := sorted(set(payload.scopes) - set(allowed))):
            raise HttpError(
                422, _("Scopes %(scopes)s cannot be granted") % {"scopes": ", ".join(unknown)}
            )
        key, raw = create_api_key(
            request_user(request),
            payload.name,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
        )
        body: dict[str, object] = APIKeyOut.model_validate(key, from_attributes=True).model_dump()
        return Status(201, {**body, "key": raw})

    @delete("/{key_id}", response={204: None})
    def revoke_key(self, request: HttpRequest, key_id: int) -> Status[None]:
        key = APIKey.objects.filter(user=request_user(request), pk=key_id).first()
        if key is None:
            raise HttpError(404, _("API key not found"))
        revoke_api_key(key)
        return Status(204, None)
