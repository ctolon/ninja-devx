"""Provider definitions and scope-owned lifecycle state."""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import Future as BuildFuture
from contextlib import (
    AsyncExitStack,
    ExitStack,
)
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Final,
)

from .contracts import Factory


class Lifetime(Enum):
    SINGLETON = auto()
    """One instance per container (cleanups run on ``close()``/``aclose()``)."""
    SCOPED = auto()
    """One instance per scope (request, task, test)."""
    TRANSIENT = auto()
    """A new instance on every resolution."""


class ProviderKind(Enum):
    PLAIN = auto()
    GENERATOR = auto()
    COROUTINE = auto()
    ASYNC_GENERATOR = auto()


class _Missing(Enum):
    MISSING = auto()


MISSING: Final = _Missing.MISSING


@dataclass(frozen=True, slots=True)
class Provider:
    factory: Factory[object]
    lifetime: Lifetime
    kind: ProviderKind


@dataclass(frozen=True, slots=True)
class Dependency:
    name: str
    key: object
    positional_only: bool
    has_default: bool


@dataclass(slots=True)
class PendingBuild:
    owner: asyncio.Task[object]
    future: BuildFuture[object] = field(default_factory=BuildFuture[object])


@dataclass(slots=True)
class ScopeState:
    instances: dict[object, object]
    pending: dict[object, PendingBuild] = field(default_factory=dict[object, PendingBuild])
    exits: ExitStack = field(default_factory=ExitStack)
    async_exits: AsyncExitStack = field(default_factory=AsyncExitStack)


def factory_kind(factory: Factory[object]) -> ProviderKind:
    if inspect.isclass(factory):
        return ProviderKind.PLAIN
    if inspect.isasyncgenfunction(factory):
        return ProviderKind.ASYNC_GENERATOR
    if inspect.iscoroutinefunction(factory):
        return ProviderKind.COROUTINE
    if inspect.isgeneratorfunction(factory):
        return ProviderKind.GENERATOR
    return ProviderKind.PLAIN
