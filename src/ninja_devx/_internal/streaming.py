"""Preflight and context-preserving cleanup for Ninja 1.x async streaming operations.

Ninja normally starts headers before advancing an async generator. Our operation
subclass consumes a private readiness marker first. A single producer task owns all
context managers, including cancellation cleanup; items are pulled only on demand.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import cast

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from ninja.operation import AsyncOperation, Operation

from ..exceptions import ControllerConfigError


@dataclass(frozen=True, slots=True)
class StreamReady:
    response: HttpResponse | None = None


@dataclass(frozen=True, slots=True)
class _Item:
    value: object = None
    done: bool = False


class PreparedStream:
    def __init__(
        self, source: AsyncIterator[object], *, on_cancel: Callable[[], None] | None = None
    ) -> None:
        self.source = source
        self.on_cancel = on_cancel
        self.loop = asyncio.get_running_loop()
        self.ready: asyncio.Future[StreamReady] = self.loop.create_future()
        self.demand: asyncio.Queue[asyncio.Future[_Item]] = asyncio.Queue(maxsize=1)
        self.task = self.loop.create_task(self._serve())
        self.task.add_done_callback(self._cleanup_result)

    @staticmethod
    def _cleanup_result(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logging.getLogger("ninja_devx").error(
                "Async stream cleanup failed", exc_info=(type(error), error, error.__traceback__)
            )

    async def _serve(self) -> None:
        current: asyncio.Future[_Item] | None = None
        try:
            marker = await anext(self.source)
            if not isinstance(marker, StreamReady):
                raise RuntimeError("Async stream did not provide its preflight marker")
            self.ready.set_result(marker)
            if marker.response is not None:
                return
            while True:
                current = await self.demand.get()
                try:
                    value = await anext(self.source)
                except StopAsyncIteration:
                    if not current.done():
                        current.set_result(_Item(done=True))
                    return
                if not current.done():
                    current.set_result(_Item(value))
                current = None
        except BaseException as exc:
            waiting: asyncio.Future[StreamReady] | asyncio.Future[_Item] | None = (
                self.ready if not self.ready.done() else current
            )
            if waiting is not None and not waiting.done():
                if isinstance(exc, asyncio.CancelledError):
                    waiting.cancel()
                else:
                    waiting.set_exception(exc)
        finally:
            close = getattr(self.source, "aclose", None)
            if close is not None:
                await close()

    def __aiter__(self) -> PreparedStream:
        return self

    async def __anext__(self) -> object:
        if self.task.done():
            raise StopAsyncIteration
        result: asyncio.Future[_Item] = self.loop.create_future()
        try:
            await self.demand.put(result)
            item = await result
            if item.done:
                raise StopAsyncIteration
            return item.value
        except asyncio.CancelledError:
            try:
                await self.aclose()
            finally:
                # Django 4.2's ASGI send path does not close the request when cancelled.
                # File cleanup is idempotent on newer Django versions as well.
                if self.on_cancel is not None:
                    self.on_cancel()
            raise
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    def close(self) -> None:
        """Django closes responses synchronously, sometimes from a worker thread."""
        if not self.task.done() and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.task.cancel)


class PreflightAsyncOperation(AsyncOperation):
    async def _async_stream_response(  # type: ignore[override]  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: HttpRequest, generator: object, temporal_response: HttpResponse
    ) -> HttpResponse | StreamingHttpResponse:
        stream = PreparedStream(cast("AsyncIterator[object]", generator), on_cancel=request.close)
        try:
            ready = await stream.ready
            if ready.response is not None:
                await stream.aclose()
                return ready.response
            response = await super()._async_stream_response(request, stream, temporal_response)
        except BaseException:
            await stream.aclose()
            raise
        original_close = response.close

        def close_response() -> None:
            stream.close()
            original_close()

        response.close = close_response  # type: ignore[method-assign]
        return response


def enable_preflight(operation: Operation) -> None:
    """Adapt only our operation; Ninja's clone preserves its concrete class."""
    if (
        not isinstance(operation, AsyncOperation)
        or getattr(operation, "stream_format", None) is None
    ):
        raise ControllerConfigError("Async generators require a Ninja streaming response schema")
    operation.__class__ = PreflightAsyncOperation
