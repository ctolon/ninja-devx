"""Security response headers as a middleware.

Applies conservative, API-friendly defaults and never overwrites a header the view already
set. HSTS is opt-in because it requires HTTPS::

    use_middleware(api, SecurityHeadersMiddleware(hsts="max-age=31536000; includeSubDomains"))
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from django.http import HttpRequest, HttpResponseBase

from .middleware import Middleware

__all__ = ["SecurityHeadersMiddleware"]

_PERMISSIONS_POLICY: Final = "geolocation=(), microphone=(), camera=()"


class SecurityHeadersMiddleware(Middleware):
    """Set security headers on every response unless the response already has them.

    :param hsts: ``Strict-Transport-Security`` value, or ``None`` to leave it unset.
    :param csp: ``Content-Security-Policy`` value, or ``None`` to leave it unset.
    :param referrer_policy: ``Referrer-Policy`` value.
    :param frame_options: ``X-Frame-Options`` value; ``None`` leaves it unset.
    :param permissions_policy: ``Permissions-Policy`` value; ``None`` leaves it unset.
    :param nosniff: set ``X-Content-Type-Options: nosniff``.
    :param extra: additional headers, applied after the defaults.
    """

    def __init__(
        self,
        *,
        hsts: str | None = None,
        csp: str | None = None,
        referrer_policy: str = "same-origin",
        frame_options: str | None = "DENY",
        permissions_policy: str | None = _PERMISSIONS_POLICY,
        nosniff: bool = True,
        extra: Mapping[str, str] | None = None,
    ) -> None:
        self.hsts = hsts
        self.csp = csp
        self.referrer_policy = referrer_policy
        self.frame_options = frame_options
        self.permissions_policy = permissions_policy
        self.nosniff = nosniff
        self.extra = dict(extra or {})

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        headers: dict[str, str | None] = {
            "X-Content-Type-Options": "nosniff" if self.nosniff else None,
            "Referrer-Policy": self.referrer_policy or None,
            "X-Frame-Options": self.frame_options,
            "Permissions-Policy": self.permissions_policy,
            "Content-Security-Policy": self.csp,
            "Strict-Transport-Security": self.hsts,
            **self.extra,
        }
        for name, value in headers.items():
            if value is not None and name not in response:
                response[name] = value
        return response
