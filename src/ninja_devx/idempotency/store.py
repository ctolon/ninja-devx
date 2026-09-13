"""Compare-and-set ownership shared across processes and cache backends."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.http import HttpResponse, JsonResponse
from django.http.response import HttpResponseBase
from django.utils import timezone
from django.utils.translation import gettext as _


@dataclass(frozen=True, slots=True)
class Claim:
    key: str
    token: uuid.UUID
    database: str
    ttl: int

    def finish(self, response: HttpResponseBase) -> None:
        from .models import IdempotencyRecord

        # A streaming or exceptional outcome is uncertain: retain ownership. Releasing
        # it could repeat a committed side effect. Reconciliation is an operator action.
        if not isinstance(response, HttpResponse):
            return
        omitted = {
            "set-cookie",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
            "x-request-id",
            "traceparent",
            "tracestate",
            "idempotent-replayed",
        }
        omitted.update(part.strip().lower() for part in response.get("Connection", "").split(","))
        IdempotencyRecord.objects.using(self.database).filter(
            pk=self.key,
            token=self.token,
            state="running",
        ).update(
            state="complete",
            status=response.status_code,
            content=response.content,
            headers=[
                (name, value) for name, value in response.items() if name.lower() not in omitted
            ],
            expires_at=timezone.now() + timedelta(seconds=self.ttl),
        )


def acquire(key: str, fingerprint: str, *, database: str, ttl: int) -> Claim | HttpResponse:
    from .models import IdempotencyRecord

    if connections[database].in_atomic_block:
        raise ImproperlyConfigured(
            "Idempotency ownership requires autocommit; use a separate database alias "
            "when ATOMIC_REQUESTS or an outer transaction is enabled"
        )
    records = IdempotencyRecord.objects.using(database)
    token = uuid.uuid4()
    record, created = records.get_or_create(
        pk=key,
        defaults={"token": token, "fingerprint": fingerprint},
    )
    if created:
        return Claim(key, token, database, ttl)
    now = timezone.now()
    if record.state == "complete" and record.expires_at is not None and record.expires_at <= now:
        claimed = records.filter(
            pk=key,
            token=record.token,
            state="complete",
            expires_at__lte=now,
        ).update(
            token=token,
            state="running",
            fingerprint=fingerprint,
            status=None,
            content=b"",
            headers=[],
            expires_at=None,
            created_at=now,
        )
        if claimed:
            return Claim(key, token, database, ttl)
        # Another request won the CAS. Do not replay the obsolete response.
        return JsonResponse(
            {"detail": _("An idempotent request is already in progress")}, status=409
        )
    if record.fingerprint != fingerprint:
        return JsonResponse(
            {"detail": _("Idempotency key was used for a different request")}, status=422
        )
    if record.state != "complete":
        return JsonResponse(
            {"detail": _("Request is in progress or requires reconciliation")}, status=409
        )
    response = HttpResponse(bytes(record.content), status=record.status)
    for name, value in cast("list[list[str]]", record.headers):
        response[name] = value
    response["Idempotent-Replayed"] = "true"
    return response
