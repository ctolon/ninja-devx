"""Bind an operation to a use case (command handler) without writing a method body.

::

    class CreatePost:
        def __init__(self, posts: PostRepository) -> None: ...
        def __call__(self, command: CreatePostCommand) -> Post: ...

    class PostController(Controller):
        create = use_case(
            post("/", response={201: PostOut}),
            CreatePost,
            command=PostIn.to_command,   # Callable[[PostIn], CreatePostCommand]
            status=201,
        )

The use case is resolved from the controller's container per request; the payload type
is the command mapper's parameter annotation.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, get_type_hints

from django.http import HttpRequest
from ninja import Status

from ..dependencies.injection import injected
from ..exceptions import ControllerConfigError

if TYPE_CHECKING:
    from .controller import Controller

__all__ = ["UseCase", "use_case"]

PayloadT = TypeVar("PayloadT")
CommandT_contra = TypeVar("CommandT_contra", contravariant=True)
CommandT = TypeVar("CommandT")
ResultT_co = TypeVar("ResultT_co", covariant=True)
ResultT = TypeVar("ResultT")

Method = Callable[..., object]


class UseCase(Protocol[CommandT_contra, ResultT_co]):
    def __call__(self, command: CommandT_contra, /) -> ResultT_co: ...


def use_case(
    decorator: Callable[[Method], Method],
    handler: Callable[..., UseCase[CommandT, ResultT] | UseCase[CommandT, Awaitable[ResultT]]],
    *,
    command: Callable[[PayloadT], CommandT],
    status: int | None = None,
) -> Method:
    """An operation method: map the payload with ``command`` and call the resolved handler.

    An ``async`` handler (``async def __call__``) makes the operation async.

    :param decorator: The operation decorator, e.g. ``post("/", response={201: OrderOut})``.
    :param handler: A class with ``__call__(command)`` (sync or async), resolved from the container.
    :param command: Maps the validated payload to the command; its parameter type is the request
        body.
    :param status: Status code of the response (default: the operation's first response).
    """
    try:
        hints = get_type_hints(command)
    except Exception as exc:
        raise ControllerConfigError(f"Cannot read the payload type of {command!r}: {exc}") from exc
    parameters = list(inspect.signature(command).parameters)
    if parameters and parameters[0] in hints:
        payload_type: object = hints[parameters[0]]
    elif parameters and (owner := _method_owner(command)) is not None:
        payload_type = owner  # ``Schema.to_command``: the payload is ``self``
    else:
        raise ControllerConfigError(f"{command!r} must annotate its payload parameter")
    handler_type = cast("type[object]", handler)
    call = getattr(handler_type, "__call__", None)  # noqa: B004
    asynchronous = inspect.iscoroutinefunction(call)

    def respond(result: object) -> object:
        return Status(status, result) if status is not None else result

    if asynchronous:

        async def async_operation(
            self: Controller, request: HttpRequest, payload: object, use_case: object
        ) -> object:
            awaited: object = await cast("Callable[[object], Awaitable[object]]", use_case)(
                command(cast("PayloadT", payload))
            )
            return respond(awaited)

        method: Method = async_operation
    else:

        def sync_operation(
            self: Controller, request: HttpRequest, payload: object, use_case: object
        ) -> object:
            return respond(
                cast("Callable[[object], object]", use_case)(command(cast("PayloadT", payload)))
            )

        method = sync_operation

    method.__annotations__ = {
        "request": HttpRequest,
        "payload": payload_type,
        "use_case": injected(handler_type),
        "return": object,
    }
    method.__name__ = getattr(handler, "__name__", "use_case")
    method.__doc__ = inspect.getdoc(handler)
    return decorator(method)


def _method_owner(function: Callable[..., object]) -> type[object] | None:
    """The class a plain function was defined in (``Schema.to_command``), if importable."""
    module = inspect.getmodule(function)
    qualname: str = getattr(function, "__qualname__", "")
    if module is None or "<locals>" in qualname or "." not in qualname:
        return None
    target: object = module
    for part in qualname.split(".")[:-1]:
        target = getattr(target, part, None)
    return target if isinstance(target, type) else None
