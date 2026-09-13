"""Plugins extend every controller they are given to, without touching the package.

::

    class TenantHeader:
        def on_operation(
            self, controller: type[Controller], name: str, spec: OperationSpec
        ) -> OperationSpec:
            return spec.with_options(openapi_extra={"parameters": [...]})

        def bindings(
            self, controller: type[Controller], name: str, spec: OperationSpec
        ) -> Sequence[ParameterBinding]:
            return ()

    options = ControllerOptions(plugins=[TenantHeader()])   # or NINJA_DEVX["PLUGINS"]

Plugins are listed explicitly; nothing is discovered through entry points.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .bindings import ParameterBinding
    from .controller import Controller
    from .operations import OperationSpec

__all__ = ["ControllerPlugin"]


@runtime_checkable
class ControllerPlugin(Protocol):
    def on_operation(
        self, controller: type[Controller], name: str, spec: OperationSpec, /
    ) -> OperationSpec:
        """Adjust an operation before it is registered (after ``customize_operation``)."""
        ...

    def bindings(
        self, controller: type[Controller], name: str, spec: OperationSpec, /
    ) -> Sequence[ParameterBinding]:
        """Extra hidden parameters for the operation."""
        ...
