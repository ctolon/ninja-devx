"""Resolution execution and startup dependency graph validation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import (
    asynccontextmanager,
    contextmanager,
)
from typing import (
    TYPE_CHECKING,
    cast,
)

from django.http import HttpRequest

from ..exceptions import CircularDependencyError, DependencyResolutionError
from .contracts import Factory
from .introspection import format_chain, type_name, unwrap_annotated, without_none
from .state import MISSING, Lifetime, PendingBuild, Provider, ProviderKind, ScopeState

if TYPE_CHECKING:
    from .container import Container


class ResolutionEngine:
    """One resolution: sync and async paths share providers, plans and lifetimes."""

    __slots__ = ("container", "state")

    def __init__(self, container: Container, state: ScopeState | None) -> None:
        self.container = container
        self.state = state

    def _prepare(self, key: object, stack: tuple[object, ...]) -> tuple[object, tuple[object, ...]]:
        if not isinstance(key, type):
            key = unwrap_annotated(key)
        if key in stack:
            raise CircularDependencyError(
                f"Circular dependency detected: {format_chain((*stack, key))}"
            )
        return key, (*stack, key)

    def _scoped_state(self, key: object, stack: tuple[object, ...]) -> ScopeState:
        if self.state is None:
            raise DependencyResolutionError(
                f"{type_name(key)} is scoped and cannot be resolved outside a scope "
                f"(resolving {format_chain(stack)}); use container.scope()"
            )
        return self.state

    # --- Sync ----------------------------------------------------------------------

    def resolve(self, key: object, stack: tuple[object, ...]) -> object:
        key, stack = self._prepare(key, stack)
        state = self.state
        if state is not None and key in state.instances:
            return state.instances[key]

        container = self.container
        provider = container.provider(key)
        if provider is None:
            factory = container.autowire(key, stack)
            return self._build(factory, ProviderKind.PLAIN, stack, self.state, key)

        match provider.lifetime:
            case Lifetime.TRANSIENT:
                return self._build(provider.factory, provider.kind, stack, self.state, key)
            case Lifetime.SCOPED:
                scoped = self._scoped_state(key, stack)
                with container.lock:
                    if key in scoped.pending:
                        raise DependencyResolutionError("An async factory is building this service")
                    if key not in scoped.instances:
                        scoped.instances[key] = self._build(
                            provider.factory, provider.kind, stack, scoped, key
                        )
                    return scoped.instances[key]
            case Lifetime.SINGLETON:
                value = container.singleton_value(key)
                if value is not MISSING:
                    return value
                with container.lock:
                    value = container.singleton_value(key)
                    if value is MISSING:
                        if key in container.root.pending:
                            raise DependencyResolutionError(
                                "An async factory is building this singleton; use aresolve()"
                            )
                        # Singletons never see the scope: no captive dependencies.
                        root = ResolutionEngine(container, None)
                        value = root._build(
                            provider.factory, provider.kind, stack, container.root, key
                        )
                        container.store_singleton(key, value)
                    return value

    def _arguments(
        self, factory: Factory[object], stack: tuple[object, ...]
    ) -> tuple[list[object], dict[str, object]]:
        args: list[object] = []
        kwargs: dict[str, object] = {}
        for dependency in self.container.plan(factory):
            key = dependency.key
            if dependency.has_default:
                # ``service: Service | None = None`` is injected only when ``Service`` is known.
                key = without_none(key)
                if not self.container.is_known(key, self.state):
                    continue
            value = self.resolve(key, stack)
            if dependency.positional_only:
                args.append(value)
            else:
                kwargs[dependency.name] = value
        return args, kwargs

    def _build(
        self,
        factory: Factory[object],
        kind: ProviderKind,
        stack: tuple[object, ...],
        owner: ScopeState | None,
        key: object,
    ) -> object:
        if kind in (ProviderKind.COROUTINE, ProviderKind.ASYNC_GENERATOR):
            raise DependencyResolutionError(
                f"{type_name(key)} has an async factory; resolve it with aresolve() or in an "
                f"async operation (resolving {format_chain(stack)})"
            )
        args, kwargs = self._arguments(factory, stack)
        if kind is ProviderKind.GENERATOR:
            manager = contextmanager(cast("Callable[..., Generator[object]]", factory))
            cleanup = owner if owner is not None else self.container.root
            return cleanup.exits.enter_context(manager(*args, **kwargs))
        return factory(*args, **kwargs)

    # --- Async ---------------------------------------------------------------------

    async def aresolve(self, key: object, stack: tuple[object, ...]) -> object:
        key, stack = self._prepare(key, stack)
        state = self.state
        if state is not None and key in state.instances:
            return state.instances[key]

        container = self.container
        provider = container.provider(key)
        if provider is None:
            factory = container.autowire(key, stack)
            return await self._abuild(factory, ProviderKind.PLAIN, stack, self.state)

        match provider.lifetime:
            case Lifetime.TRANSIENT:
                return await self._abuild(provider.factory, provider.kind, stack, self.state)
            case Lifetime.SCOPED:
                scoped = self._scoped_state(key, stack)
                return await self._cached_async(key, provider, stack, scoped)
            case Lifetime.SINGLETON:
                root = ResolutionEngine(container, None)
                return await root._cached_async(key, provider, stack, container.root)

    async def _cached_async(
        self, key: object, provider: Provider, stack: tuple[object, ...], owner: ScopeState
    ) -> object:
        container = self.container
        task = cast("asyncio.Task[object]", asyncio.current_task())
        with container.lock:
            if key in owner.instances:
                return owner.instances[key]
            pending = owner.pending.get(key)
            building = pending is None
            if pending is None:
                pending = owner.pending[key] = PendingBuild(task)
            else:
                # A task may join another build, but cannot wait on itself or a
                # chain which already waits on it (including different providers).
                container.track_waiter(task, pending.owner, stack)
        if not building:
            try:
                # A cancelled waiter must not cancel a shared factory's result.
                return await asyncio.shield(asyncio.wrap_future(pending.future))
            finally:
                container.release_waiter(task)
        try:
            value = await self._abuild(provider.factory, provider.kind, stack, owner)
            with container.lock:
                owner.instances[key] = value
                pending.future.set_result(value)
            return value
        except BaseException as exc:
            pending.future.set_exception(exc)
            raise
        finally:
            with container.lock:
                owner.pending.pop(key, None)

    async def _abuild(
        self,
        factory: Factory[object],
        kind: ProviderKind,
        stack: tuple[object, ...],
        owner: ScopeState | None,
    ) -> object:
        args: list[object] = []
        kwargs: dict[str, object] = {}
        for dependency in self.container.plan(factory):
            key = dependency.key
            if dependency.has_default:
                key = without_none(key)
                if not self.container.is_known(key, self.state):
                    continue
            value = await self.aresolve(key, stack)
            if dependency.positional_only:
                args.append(value)
            else:
                kwargs[dependency.name] = value
        cleanup = owner if owner is not None else self.container.root
        match kind:
            case ProviderKind.COROUTINE:
                awaited: object = await cast("Callable[..., asyncio.Future[object]]", factory)(
                    *args, **kwargs
                )
                return awaited
            case ProviderKind.ASYNC_GENERATOR:
                amanager = asynccontextmanager(
                    cast("Callable[..., AsyncGenerator[object]]", factory)
                )
                return await cleanup.async_exits.enter_async_context(amanager(*args, **kwargs))
            case ProviderKind.GENERATOR:
                manager = contextmanager(cast("Callable[..., Generator[object]]", factory))
                return cleanup.exits.enter_context(manager(*args, **kwargs))
            case ProviderKind.PLAIN:
                return factory(*args, **kwargs)


class DependencyChecker:
    """Walks a dependency graph using only types (nothing is instantiated)."""

    __slots__ = ("asynchronous", "container", "seen")

    def __init__(self, container: Container, asynchronous: bool) -> None:
        self.container = container
        self.asynchronous = asynchronous
        self.seen: set[object] = set()

    def check(self, key: object, stack: tuple[object, ...], owner: Lifetime) -> None:
        if not isinstance(key, type):
            key = unwrap_annotated(key)
        if key in stack:
            raise CircularDependencyError(
                f"Circular dependency detected: {format_chain((*stack, key))}"
            )
        stack = (*stack, key)
        if key is HttpRequest:
            if owner is Lifetime.SINGLETON:
                raise DependencyResolutionError(
                    f"A singleton depends on the request ({format_chain(stack)})"
                )
            return
        provider = self.container.provider(key)
        if provider is None:
            factory = self.container.autowire(key, stack)
            lifetime, kind = Lifetime.TRANSIENT, ProviderKind.PLAIN
        else:
            factory, lifetime, kind = provider.factory, provider.lifetime, provider.kind
        if lifetime is Lifetime.SCOPED and owner is Lifetime.SINGLETON:
            raise DependencyResolutionError(
                f"A singleton depends on the scoped {type_name(key)} ({format_chain(stack)}); "
                "make the dependency a singleton or the dependent scoped"
            )
        if not self.asynchronous and kind in (ProviderKind.COROUTINE, ProviderKind.ASYNC_GENERATOR):
            raise DependencyResolutionError(
                f"{type_name(key)} has an async factory but is needed by a sync operation "
                f"({format_chain(stack)})"
            )
        marker = (key, owner is Lifetime.SINGLETON)
        if marker in self.seen:
            return
        self.seen.add(marker)
        effective = Lifetime.SINGLETON if Lifetime.SINGLETON in (owner, lifetime) else lifetime
        for dependency in self.container.plan(factory):
            dependency_key = dependency.key
            if dependency.has_default:
                dependency_key = without_none(dependency_key)
                if not self.container.is_known(dependency_key, None):
                    continue
            self.check(dependency_key, stack, effective)
