"""Dependency registration and container ownership."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Generator, Mapping
from contextlib import (
    contextmanager,
)
from threading import RLock
from typing import (
    TypeVar,
    cast,
    get_type_hints,
)

from django.http import HttpRequest

from .._internal.compat import signature
from .._internal.generics import substitute, type_arguments
from ..exceptions import CircularDependencyError, DependencyResolutionError
from .contracts import AsyncRequestScopeProvider as AsyncRequestScopeProvider
from .contracts import AsyncResolver as AsyncResolver
from .contracts import CheckableContainer as CheckableContainer
from .contracts import ContainerLike as ContainerLike
from .contracts import Factory, T
from .contracts import RequestScopeProvider as RequestScopeProvider
from .contracts import Resolver as Resolver
from .engine import DependencyChecker, ResolutionEngine
from .introspection import check_instance, format_chain, type_name
from .scope import Scope as Scope
from .state import MISSING, Dependency, Provider, ScopeState, factory_kind
from .state import Lifetime as Lifetime


class Container:
    """Constructor-injection container with singleton, scoped and transient lifetimes.

    - Unregistered concrete classes are auto-wired as transient.
    - Abstract classes, protocols and builtins must be registered.
    - Factories may be generators (cleanup after ``yield``), coroutines or async
      generators; async ones need ``aresolve``/``async with container.scope()``.
    - ``scope()`` opens a scope anywhere (tasks, commands, tests); inside
      ``request_scope(request)`` the ``HttpRequest`` itself is injectable.

    Keys are typed as ``Callable[..., T]`` rather than ``type[T]`` so abstract classes
    and protocols are accepted by mypy.
    """

    def __init__(self) -> None:
        self._providers: dict[object, Provider] = {}
        self._plans: dict[Factory[object], tuple[Dependency, ...]] = {}
        self._autowired: set[object] = set()
        self._lock = RLock()
        self._waiting: dict[asyncio.Task[object], asyncio.Task[object]] = {}
        self._root = ScopeState({})

    # Registration -----------------------------------------------------------

    def singleton(self, key: Factory[T], factory: Factory[T] | None = None) -> None:
        """One instance per container, built from ``factory`` (defaults to ``key``).

        :param key: What is requested (a class, abstract class, protocol or generic alias).
        :param factory: Builds it: a class, function, generator or async variant (default: ``key``
            itself).
        """
        self.register(key, factory, lifetime=Lifetime.SINGLETON)

    def scoped(self, key: Factory[T], factory: Factory[T] | None = None) -> None:
        """One instance per scope; unavailable outside of one.

        :param key: What is requested.
        :param factory: Builds it once per scope (request, or ``container.scope()``); generators
            clean up when the scope closes.
        """
        self.register(key, factory, lifetime=Lifetime.SCOPED)

    def transient(self, key: Factory[T], factory: Factory[T] | None = None) -> None:
        """A new instance on every resolution.

        :param key: What is requested.
        :param factory: Builds a new value every time it is resolved.
        """
        self.register(key, factory, lifetime=Lifetime.TRANSIENT)

    def register(
        self, key: Factory[T], factory: Factory[T] | None = None, *, lifetime: Lifetime
    ) -> None:
        """
        :param key: What is requested.
        :param factory: Builds it (default: ``key`` itself).
        :param lifetime: ``Lifetime.SINGLETON``, ``SCOPED`` or ``TRANSIENT``.
        """
        resolved = factory or key
        with self._lock:
            self._providers[key] = Provider(resolved, lifetime, factory_kind(resolved))
            self._root.instances.pop(key, None)

    def instance(self, key: Factory[object], value: object) -> None:
        """Register an already constructed object.

        ``value`` is checked against ``key`` when the key is a (runtime-checkable) class:
        type checkers cannot infer it from abstract classes and protocols.

        :param key: What is requested.
        :param value: The object returned; checked against class keys at registration.
        """
        check_instance(key, value)
        with self._lock:
            self.register(key, lambda: value, lifetime=Lifetime.SINGLETON)
            self._root.instances[key] = value

    @contextmanager
    def override(self, key: Factory[object], value: object) -> Generator[None]:
        """Temporarily replace a dependency, e.g. with a fake in tests.

        Singletons built earlier keep the dependency they were built with.

        :param key: The dependency to replace.
        :param value: The replacement, until the ``with`` block ends.
        """
        with self._lock:
            previous_provider = self._providers.get(key)
            previous_instance = self._root.instances.get(key, MISSING)
            self.instance(key, value)
        try:
            yield
        finally:
            with self._lock:
                self._providers.pop(key, None)
                self._root.instances.pop(key, None)
                if previous_provider is not None:
                    self._providers[key] = previous_provider
                if previous_instance is not MISSING:
                    self._root.instances[key] = previous_instance

    # Scopes ------------------------------------------------------------------

    def resolve(self, key: Factory[T], /) -> T:
        """Resolve outside of a scope (scoped dependencies are rejected).

        :param key: What to build (sync factories only).
        """
        return cast(T, ResolutionEngine(self, None).resolve(key, ()))

    async def aresolve(self, key: Factory[T], /) -> T:
        """
        :param key: What to build (sync and async factories).
        """
        return cast(T, await ResolutionEngine(self, None).aresolve(key, ()))

    def scope(self, values: Mapping[object, object] | None = None, /) -> Scope:
        """A scope for ``scoped`` services, closed (with cleanups) when the block exits.

        ``values`` are available for injection by key, e.g.
        ``container.scope({RequestContext[User, None]: context})``.

        :param values: Values available to scoped factories, e.g. ``{RequestContext[User, None]:
            context}``.
        """
        return Scope(self, ScopeState(dict(values or {})))

    def request_scope(self, request: HttpRequest, /) -> Scope:
        return self.scope({HttpRequest: request})

    def arequest_scope(self, request: HttpRequest, /) -> Scope:
        return self.scope({HttpRequest: request})

    def close(self) -> None:
        """Run the cleanup of generator singletons."""
        self._root.exits.close()

    async def aclose(self) -> None:
        await self._root.async_exits.aclose()
        self._root.exits.close()

    # Validation ----------------------------------------------------------------

    def check(self, key: Factory[object], /, *, asynchronous: bool = False) -> None:
        """Validate the graph of ``key`` without building anything.

        Raises ``DependencyResolutionError`` for keys that cannot be resolved,
        singletons depending on scoped services, and async factories needed by a sync
        resolution.

        :param key: What will be requested.
        :param asynchronous: Whether async operations will request it (allows async factories).
        """
        DependencyChecker(self, asynchronous).check(key, (), Lifetime.TRANSIENT)

    # Internals shared with the engine ------------------------------------------

    def track_waiter(
        self, waiter: asyncio.Task[object], owner: asyncio.Task[object], stack: tuple[object, ...]
    ) -> None:
        with self._lock:
            cursor = owner
            while True:
                if cursor is waiter:
                    raise CircularDependencyError(
                        f"Circular async dependency: {format_chain(stack)}"
                    )
                next_task = self._waiting.get(cursor)
                if next_task is None:
                    break
                cursor = next_task
            self._waiting[waiter] = owner

    def release_waiter(self, waiter: asyncio.Task[object]) -> None:
        with self._lock:
            self._waiting.pop(waiter, None)

    def provider(self, key: object) -> Provider | None:
        return self._providers.get(key)

    def plan(self, factory: Factory[object]) -> tuple[Dependency, ...]:
        """Inspect a factory's parameters once and cache the result."""
        plan = self._plans.get(factory)
        if plan is not None:
            return plan

        name = type_name(factory)
        target: object = factory.__init__ if inspect.isclass(factory) else factory
        try:
            parameters = signature(factory).parameters.values()
            hints: dict[str, object] = get_type_hints(target, include_extras=True)
        except Exception as exc:
            raise DependencyResolutionError(
                f"Cannot inspect dependencies of {name}: {exc}"
            ) from exc

        # Generic classes bound by a subclass (``Service(Generic[T])`` used as
        # ``ArticleService(Service[Article])``) get their TypeVars substituted.
        typevars: Mapping[TypeVar, object] = (
            type_arguments(factory) if inspect.isclass(factory) else {}
        )
        dependencies: list[Dependency] = []
        for parameter in parameters:
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            has_default = parameter.default is not parameter.empty
            if parameter.name not in hints:
                if has_default:
                    continue
                raise DependencyResolutionError(
                    f"Cannot resolve parameter {parameter.name!r} of {name}: "
                    "missing type annotation"
                )
            hint = hints[parameter.name]
            dependencies.append(
                Dependency(
                    name=parameter.name,
                    key=substitute(hint, typevars) if typevars else hint,
                    positional_only=parameter.kind is parameter.POSITIONAL_ONLY,
                    has_default=has_default,
                )
            )

        plan = tuple(dependencies)
        self._plans[factory] = plan
        return plan

    def autowire(self, key: object, stack: tuple[object, ...]) -> Factory[object]:
        if key in self._autowired:
            return cast("Factory[object]", key)
        reason = None
        if not inspect.isclass(key):
            reason = "it is not a class"
        elif getattr(key, "_is_protocol", False):
            reason = "it is a Protocol"
        elif inspect.isabstract(key):
            reason = "it is abstract"
        elif key.__module__ == "builtins":
            reason = "it is a builtin type"
        elif key is HttpRequest:
            reason = "the request is only available inside a request scope"
        if reason is not None:
            raise DependencyResolutionError(
                f"No provider registered for {type_name(key)} and it cannot be auto-wired "
                f"because {reason} (resolving {format_chain(stack)}). "
                "Register it with container.singleton(), scoped(), transient() or instance()."
            )
        self._autowired.add(key)
        return cast("Factory[object]", key)

    def is_known(self, key: object, state: ScopeState | None) -> bool:
        return key in self._providers or (state is not None and key in state.instances)

    def singleton_value(self, key: object) -> object:
        return self._root.instances.get(key, MISSING)

    def store_singleton(self, key: object, value: object) -> None:
        self._root.instances[key] = value

    @property
    def lock(self) -> RLock:
        return self._lock

    @property
    def root(self) -> ScopeState:
        return self._root
