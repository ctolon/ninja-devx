"""Outbound URL safety for webhooks: no requests to internal networks (SSRF).

Endpoint URLs come from API clients, so a URL like ``http://169.254.169.254/`` (cloud
metadata), ``http://localhost:8000/admin`` or ``http://10.0.0.5/`` would make the worker
call internal services. Two layers stop that:

- ``check_url`` rejects bad schemes, credentials in URLs, and literal private addresses or
  ``localhost`` names when an endpoint is saved (no DNS lookup, so it works offline);
- ``SafeHTTPTransport`` checks the address it actually connected to, after DNS resolution,
  so a public name resolving to a private address (DNS rebinding) is refused too. Proxies
  from the environment are ignored.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, cast
from urllib.parse import urlsplit

from django.utils.translation import gettext as _

__all__ = ["SafeHTTPTransport", "URLPolicy", "UnsafeURL", "check_url", "is_public_address"]

_LOCAL_NAMES: Final = ("localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback")


class UnsafeURL(OSError):
    """The URL or the address it resolves to is not allowed."""


@dataclass(frozen=True, slots=True)
class URLPolicy:
    """Where webhooks may be sent."""

    allow_http: bool = False
    """Accept ``http://`` URLs (default: HTTPS only)."""
    allow_private_networks: bool = False
    """Accept loopback, private, link-local and other non-public addresses (development)."""


def is_public_address(address: str) -> bool:
    """Whether ``address`` is a globally routable unicast IP (IPv4-mapped IPv6 unwrapped)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, policy: URLPolicy | None = None) -> None:
    """Raise ``UnsafeURL`` when ``url`` breaks ``policy`` (static checks, no DNS).

    :param url: The endpoint URL.
    :param policy: What is allowed.
    """
    policy = policy or URLPolicy()
    parts = urlsplit(url)
    allowed = {"https", "http"} if policy.allow_http else {"https"}
    if parts.scheme not in allowed:
        raise UnsafeURL(
            _("URL scheme must be %(schemes)s") % {"schemes": " / ".join(sorted(allowed))}
        )
    if parts.username or parts.password:
        raise UnsafeURL(_("URLs must not contain credentials"))
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL(_("URL has no host"))
    if policy.allow_private_networks:
        return
    if host in _LOCAL_NAMES or host.endswith(".localhost"):
        raise UnsafeURL(_("URL points to the local machine"))
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return  # a name: checked again after resolution, when connecting
    if not is_public_address(host):
        raise UnsafeURL(_("URL points to a private or reserved address"))


def _checked_create_connection(policy: URLPolicy) -> Callable[..., socket.socket]:
    def create_connection(
        address: tuple[str, int],
        timeout: float | None = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        sock = socket.create_connection(address, timeout, source_address)
        peer = str(sock.getpeername()[0])
        if not policy.allow_private_networks and not is_public_address(peer):
            sock.close()
            raise UnsafeURL(_("%(host)s resolved to a non-public address") % {"host": address[0]})
        return sock

    return create_connection


def _connection_factory(
    base: type[http.client.HTTPConnection], policy: URLPolicy
) -> Callable[..., http.client.HTTPConnection]:
    make = cast("Callable[..., http.client.HTTPConnection]", base)

    def build(host: str, **kwargs: object) -> http.client.HTTPConnection:
        connection = make(host, **kwargs)
        setattr(connection, "_create_connection", _checked_create_connection(policy))  # noqa: B010
        return connection

    return build


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, policy: URLPolicy) -> None:
        super().__init__()
        self.policy = policy

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_connection_factory(http.client.HTTPConnection, self.policy), req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, policy: URLPolicy) -> None:
        super().__init__()
        self.policy = policy

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        connection = _connection_factory(http.client.HTTPSConnection, self.policy)
        return self.do_open(connection, req, context=getattr(self, "_context", None))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


@dataclass(frozen=True, slots=True)
class SafeHTTPTransport:
    """The default webhook transport: ``urllib``, no redirects, no environment proxies,
    and ``policy`` enforced on the URL and on the connected address."""

    policy: URLPolicy = URLPolicy()
    """Where requests may go."""

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
        check_url(url, self.policy)
        opener = urllib.request.OpenerDirector()
        for handler in (
            urllib.request.ProxyHandler({}),
            urllib.request.UnknownHandler(),
            urllib.request.HTTPDefaultErrorHandler(),
            urllib.request.HTTPErrorProcessor(),
            _NoRedirect(),
            _HTTPHandler(self.policy),
            _HTTPSHandler(self.policy),
        ):
            opener.add_handler(handler)
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with opener.open(request, timeout=timeout) as response:
                status: int = response.status
                return status
        except urllib.error.HTTPError as exc:
            return exc.code
        except urllib.error.URLError as exc:
            reason: object = exc.reason
            if isinstance(reason, OSError):
                raise reason from exc
            raise OSError(str(reason)) from exc
