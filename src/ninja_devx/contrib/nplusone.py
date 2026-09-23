"""Runtime N+1 detection: an adapter over `django-zeal
<https://github.com/taobojlen/django-zeal>`_.

Detection stays entirely in ``zeal``; nothing here re-implements query counting. Install
the middleware so every request runs inside zeal's tracking context (``zeal.setup()``/
``zeal.teardown()``, the primitives ``zeal.zeal_context()`` wraps), and when zeal raises
``NPlusOneError`` the middleware rewrites its message to name the controller and point at
the fix::

    install(api, [NPlusOnePlugin()])

or, without the plugin system::

    use_middleware(api, NPlusOneMiddleware())

Requires ``"zeal"`` in ``INSTALLED_APPS`` and the ``zeal`` extra
(``pip install ninja-devx[zeal]``, ``django-zeal>=2``); tune detection with zeal's own
settings (``ZEAL_RAISE``, ``ZEAL_NPLUSONE_THRESHOLD``, ``ZEAL_ALLOWLIST``, ...). Everything
here is a no-op when ``zeal`` is not installed, so it is safe to add unconditionally.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from collections.abc import Generator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Final, Protocol, cast

from django.http import HttpRequest, HttpResponseBase

from ..http.middleware import Middleware
from ..plugins import APIPlugin
from ..routing.hooks import get_operation

__all__ = [
    "NPlusOneMiddleware",
    "NPlusOnePlugin",
    "explain_n_plus_one",
    "zeal_installed",
    "zeal_strict",
]

_MESSAGE: Final = re.compile(r"N\+1 detected on (?P<model>[\w.]+)\.(?P<field>\w+)")
_TOKEN_ATTR: Final = "_ninja_devx_zeal_token"


class _Zeal(Protocol):
    """The slice of ``django-zeal``'s public API this adapter drives."""

    NPlusOneError: type[BaseException]

    def setup(self) -> object: ...

    def teardown(self, token: object = None) -> None: ...

    def zeal_context(self) -> AbstractContextManager[None]: ...


def zeal_installed() -> bool:
    """Whether ``django-zeal`` is importable."""
    return importlib.util.find_spec("zeal") is not None


def _zeal() -> _Zeal:
    return cast("_Zeal", importlib.import_module("zeal"))


def explain_n_plus_one(message: str, *, controller: str | None = None) -> str:
    """Rewrite a zeal ``NPlusOneError`` message to name ``controller`` and suggest a fix.

    zeal's own message is ``"N+1 detected on <app>.<Model>.<field> at <file>:<line> in
    <function>"``; this appends the controller (when known) and the
    ``ninja_devx.crud.optimization`` names that fix it. Messages zeal did not produce (no
    ``model.field``) are returned unchanged.

    :param message: ``str(exc)`` from the ``NPlusOneError`` zeal raised.
    :param controller: Qualified name of the controller handling the request, if known.
    """
    match = _MESSAGE.search(message)
    if match is None:
        return message
    field = match.group("field")
    where = f" in {controller}" if controller else ""
    return (
        f"{message}\n"
        f"Fix{where}: add related = ({field!r},) to the controller, or "
        f"@requires_related({field!r}) on the resolver that reads it "
        "(see ninja_devx.crud.optimization)."
    )


class NPlusOneMiddleware(Middleware):
    """Runs the request inside zeal's tracking context and explains its errors.

    A no-op when ``zeal`` is not installed.
    """

    def process_request(self, request: HttpRequest) -> None:
        if zeal_installed():
            request.__dict__[_TOKEN_ATTR] = _zeal().setup()
        return None

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        self._teardown(request)
        return response

    def process_exception(self, request: HttpRequest, exception: BaseException) -> None:
        self._teardown(request)
        if not zeal_installed():
            return
        error_class = _zeal().NPlusOneError
        if isinstance(exception, error_class) and exception.args:
            operation = get_operation(request)
            controller = operation.qualname if operation is not None else None
            message = explain_n_plus_one(str(exception.args[0]), controller=controller)
            exception.args = (message, *exception.args[1:])

    @staticmethod
    def _teardown(request: HttpRequest) -> None:
        if not zeal_installed():
            return
        _zeal().teardown(request.__dict__.pop(_TOKEN_ATTR, None))


class NPlusOnePlugin(APIPlugin):
    """Adds :class:`NPlusOneMiddleware` to every operation of the API."""

    def middleware(self) -> Sequence[Middleware]:
        return (NPlusOneMiddleware(),)


@contextmanager
def zeal_strict() -> Generator[None]:
    """``zeal.zeal_context()`` forced to raise, regardless of ``settings.ZEAL_RAISE``.

    Backs the ``strict_queries`` pytest fixture (``ninja_devx.testing``). Call
    :func:`zeal_installed` first: this assumes ``zeal`` is installed and does not skip.
    """
    from django.test import override_settings

    with override_settings(ZEAL_RAISE=True), _zeal().zeal_context():
        yield
