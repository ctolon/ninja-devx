"""Turning an operation spec into a Ninja view and registering it on a Ninja ``Router``."""

from __future__ import annotations

import inspect
import time
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import (
    AbstractContextManager,
    AsyncExitStack,
    ExitStack,
)
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Literal,
    TypeVar,
    cast,
)

from asgiref.sync import sync_to_async
from django.core.exceptions import SynchronousOnlyOperation
from django.db import transaction
from django.http import HttpRequest

from .._internal.types import (
    MethodFunction,
    ViewFunction,
)
from ..exceptions import AsyncLazyAccessError, BlockingCallWarning
from ..http.errors import ErrorMap
from ..idempotency.policy import Policy as IdempotencyPolicy
from ..security.permissions import (
    AnyPermission,
    acheck_permissions,
    bind_permissions,
    check_permissions,
)
from .bindings import Arguments, ParameterBinding
from .hooks import AsyncOperationHook, OperationHook, OperationInfo, bind_operation

if TYPE_CHECKING:
    from ..dependencies.instances import InstanceProvider, Invocation
    from .controller import Controller

__all__ = ["InvocationPlan", "make_view"]

Hook = OperationHook | AsyncOperationHook


@dataclass(frozen=True, slots=True)
class InvocationPlan:
    """Everything a view needs per call, decided once at registration."""

    func: MethodFunction
    operation: OperationInfo
    instances: InstanceProvider
    permissions: tuple[AnyPermission, ...]
    hooks: tuple[Hook, ...]
    bindings: tuple[ParameterBinding, ...]
    atomic: bool | Literal["durable"]
    database: str | None
    before: bool
    after: bool
    errors: ErrorMap
    blocking_ms: float | None
    idempotency: IdempotencyPolicy | None

    def transaction(self) -> AbstractContextManager[object]:
        return transaction.atomic(using=self.database, durable=self.atomic == "durable")


def make_view(plan: InvocationPlan) -> ViewFunction:
    """Create a view with the same sync/async/generator nature as the method."""
    func = plan.func
    if inspect.isasyncgenfunction(func):
        return _async_generator_view(plan)
    if inspect.iscoroutinefunction(func):
        return _async_view(plan)
    if inspect.isgeneratorfunction(func):
        return _generator_view(plan)
    return _sync_view(plan)


def _sync_view(plan: InvocationPlan) -> ViewFunction:
    func = plan.func
    errors = plan.errors
    operation = plan.operation
    factory = plan.instances.factory()
    simple = not (
        plan.hooks
        or plan.permissions
        or plan.bindings
        or plan.atomic
        or plan.before
        or plan.after
        or plan.idempotency
    )
    if simple and factory is not None:

        def fast_view(request: HttpRequest, /, *args: object, **kwargs: object) -> object:
            bind_operation(request, operation)
            try:
                return func(factory(), request, *args, **kwargs)
            except Exception as exc:
                return _map_or_raise(errors, request, exc)

        return fast_view

    def view(request: HttpRequest, /, *args: object, **kwargs: object) -> object:
        bind_operation(request, operation)
        try:
            if not plan.hooks and not plan.atomic:
                return _invoke_sync(plan, request, args, kwargs)
            with ExitStack() as stack:
                _enter_sync_hooks(plan, request, stack)
                if plan.atomic and plan.idempotency is None:
                    stack.enter_context(plan.transaction())
                return _invoke_sync(plan, request, args, kwargs)
        except Exception as exc:
            return _map_or_raise(errors, request, exc)

    return view


def _invoke_sync(
    plan: InvocationPlan, request: HttpRequest, args: tuple[object, ...], kwargs: Arguments
) -> object:
    if plan.permissions:
        check_permissions(request, plan.permissions)
        bind_permissions(request, plan.permissions)
    with plan.instances.sync(request) as invocation:
        controller = _prepare_sync(plan, invocation, kwargs)
        if plan.idempotency is not None:
            if request.headers.get(plan.idempotency.header):
                controller.authorize_replay(request, plan.operation, kwargs)
            outcome = plan.idempotency.before(request)
            if outcome is not None:
                return outcome
        with ExitStack() as stack:
            if plan.atomic and plan.idempotency is not None:
                stack.enter_context(plan.transaction())
            result = plan.func(controller, request, *args, **kwargs)
            if plan.after:
                result = controller.after_operation(request, plan.operation, result)
            return result


def _prepare_sync(plan: InvocationPlan, invocation: Invocation, kwargs: Arguments) -> Controller:
    for binding in plan.bindings:
        value = binding.resolve(invocation, kwargs)
        if binding.name is not None:
            kwargs[binding.name] = value
    controller = invocation.controller
    if plan.before:
        controller.before_operation(invocation.request, plan.operation)
    return controller


def _generator_view(plan: InvocationPlan) -> ViewFunction:
    func = plan.func

    def generator_view(request: HttpRequest, /, *args: object, **kwargs: object) -> object:
        bind_operation(request, plan.operation)
        # Everything that can fail runs before the stream starts, so errors become
        # regular responses; hooks and the DI scope stay open while streaming.
        stack = ExitStack()
        try:
            _enter_sync_hooks(plan, request, stack)
            if plan.permissions:
                check_permissions(request, plan.permissions)
                bind_permissions(request, plan.permissions)
            invocation = stack.enter_context(plan.instances.sync(request))
            controller = _prepare_sync(plan, invocation, kwargs)
            items = cast("Iterator[object]", func(controller, request, *args, **kwargs))
        except Exception as exc:
            stack.close()
            return _map_or_raise(plan.errors, request, exc)
        except BaseException:
            stack.close()
            raise

        def stream() -> Iterator[object]:
            with stack:
                yield from items

        return stream()

    return generator_view


def _async_view(plan: InvocationPlan) -> ViewFunction:
    func = plan.func

    async def async_view(request: HttpRequest, /, *args: object, **kwargs: object) -> object:
        bind_operation(request, plan.operation)
        try:
            if not plan.hooks:
                return await _invoke_async(plan, request, args, kwargs)
            async with AsyncExitStack() as stack:
                await _enter_async_hooks(plan, request, stack)
                return await _invoke_async(plan, request, args, kwargs)
        except SynchronousOnlyOperation as exc:
            raise AsyncLazyAccessError(plan.operation.qualname) from exc
        except Exception as exc:
            return _map_or_raise(plan.errors, request, exc)

    async def _invoke_async(
        plan: InvocationPlan, request: HttpRequest, args: tuple[object, ...], kwargs: Arguments
    ) -> object:
        if plan.permissions:
            await acheck_permissions(request, plan.permissions)
            bind_permissions(request, plan.permissions)
        async with plan.instances.asynchronous(request) as invocation:
            controller = await _prepare_async(plan, invocation, kwargs)
            if plan.idempotency is not None:
                if request.headers.get(plan.idempotency.header):
                    authorize = controller.authorize_replay
                    if inspect.iscoroutinefunction(authorize):
                        await authorize(request, plan.operation, kwargs)
                    else:
                        await sync_to_async(authorize)(request, plan.operation, kwargs)
                outcome = await sync_to_async(plan.idempotency.before)(request)
                if outcome is not None:
                    return outcome
            result: object = await cast(
                "Awaitable[object]", func(controller, request, *args, **kwargs)
            )
            if plan.after:
                result = await _maybe_await(
                    controller.after_operation(request, plan.operation, result)
                )
            return result

    return async_view


async def _prepare_async(
    plan: InvocationPlan, invocation: Invocation, kwargs: Arguments
) -> Controller:
    for binding in plan.bindings:
        if binding.aresolve is not None:
            value = await binding.aresolve(invocation, kwargs)
        else:
            value = _timed(plan, f"binding {binding.name}", binding.resolve, invocation, kwargs)
        if binding.name is not None:
            kwargs[binding.name] = value
    controller = invocation.controller
    if plan.before:
        await _maybe_await(controller.before_operation(invocation.request, plan.operation))
    return controller


def _async_generator_view(plan: InvocationPlan) -> ViewFunction:
    from .._internal.streaming import StreamReady

    async def async_generator_view(
        request: HttpRequest, /, *args: object, **kwargs: object
    ) -> AsyncIterator[object]:
        bind_operation(request, plan.operation)
        stack = AsyncExitStack()
        try:
            await _enter_async_hooks(plan, request, stack)
            if plan.permissions:
                await acheck_permissions(request, plan.permissions)
                bind_permissions(request, plan.permissions)
            invocation = await stack.enter_async_context(plan.instances.asynchronous(request))
            controller = await _prepare_async(plan, invocation, kwargs)
        except Exception as exc:
            await stack.aclose()
            response = plan.errors.response(request, exc)
            if response is None:
                raise
            yield StreamReady(response)
            return
        except BaseException:
            await stack.aclose()
            raise

        async with stack:
            yield StreamReady()
            items = cast("AsyncIterator[object]", plan.func(controller, request, *args, **kwargs))
            try:
                async for item in items:
                    yield item
            finally:
                close = getattr(items, "aclose", None)
                if close is not None:
                    await close()

    return async_generator_view


# --- Helpers ---------------------------------------------------------------------


def _map_or_raise(errors: ErrorMap, request: HttpRequest, exc: Exception) -> object:
    response = errors.response(request, exc)
    if response is None:
        raise exc
    return response


def _enter_sync_hooks(plan: InvocationPlan, request: HttpRequest, stack: ExitStack) -> None:
    for hook in plan.hooks:
        if isinstance(hook, OperationHook):
            stack.enter_context(hook.around(request, plan.operation))


async def _enter_async_hooks(
    plan: InvocationPlan, request: HttpRequest, stack: AsyncExitStack
) -> None:
    for hook in plan.hooks:
        if isinstance(hook, AsyncOperationHook):
            await stack.enter_async_context(hook.around_async(request, plan.operation))
        elif getattr(hook, "blocking", False):
            manager = hook.around(request, plan.operation)
            await stack.enter_async_context(_threaded(manager))
        else:
            manager = hook.around(request, plan.operation)
            _timed(plan, f"hook {type(hook).__name__}", manager.__enter__)
            stack.push(manager.__exit__)


class _threaded:
    """Runs a sync context manager's enter/exit in a thread (for blocking hooks)."""

    __slots__ = ("manager",)

    def __init__(self, manager: AbstractContextManager[None]) -> None:
        self.manager = manager

    async def __aenter__(self) -> None:
        await sync_to_async(self.manager.__enter__)()

    async def __aexit__(self, *exc_info: object) -> bool | None:
        exit_method = cast("Callable[..., bool | None]", self.manager.__exit__)
        result: bool | None = await sync_to_async(exit_method)(*exc_info)
        return result


R = TypeVar("R")


def _timed(plan: InvocationPlan, what: str, function: Callable[..., R], *args: object) -> R:
    """Call sync code on the event loop, warning when it blocks longer than the threshold."""
    if plan.blocking_ms is None:
        return function(*args)
    started = time.perf_counter()
    try:
        return function(*args)
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        if elapsed > plan.blocking_ms:
            warnings.warn(
                f"{plan.operation.qualname}: {what} blocked the event loop for {elapsed:.1f} ms; "
                "make it async or mark it blocking",
                BlockingCallWarning,
                stacklevel=2,
            )


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        awaited: object = await value
        return awaited
    return value
