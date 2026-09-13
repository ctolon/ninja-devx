"""Parameters whose value is computed per call instead of passed by Ninja.

A binding exposes its own parameters to Ninja (e.g. a ``pk`` path parameter), or none
(injected services), and turns them into the value the method receives.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .._internal.generics import LazyAnnotation

if TYPE_CHECKING:
    from ..dependencies.container import ContainerLike
    from ..dependencies.instances import Invocation
    from .controller import Controller
    from .operations import OperationSpec

__all__ = ["Arguments", "BindingMarker", "ParameterBinding"]

Arguments = MutableMapping[str, object]


@dataclass(frozen=True, slots=True)
class ParameterBinding:
    """How one method parameter (or a hidden one, when ``name`` is ``None``) is produced.

    ``resolve`` pops its exposed parameters from the arguments and returns the value;
    ``aresolve`` is used by async operations when given. ``check`` validates the binding
    against the container when the router is built.
    """

    name: str | None
    parameters: tuple[inspect.Parameter, ...]
    resolve: Callable[[Invocation, Arguments], object]
    aresolve: Callable[[Invocation, Arguments], Awaitable[object]] | None = None
    documented_errors: frozenset[int] = frozenset()
    needs_resolver: bool = False
    check: Callable[[ContainerLike | None, bool], None] | None = None


class BindingMarker(LazyAnnotation):
    """``Annotated`` metadata that turns a parameter into a ``ParameterBinding``."""

    def bind(
        self,
        parameter: inspect.Parameter,
        annotation: object,
        controller: type[Controller],
        spec: OperationSpec,
    ) -> ParameterBinding:
        raise NotImplementedError

    def resolve(self, annotation: object, controller: type[object]) -> object:
        return annotation
