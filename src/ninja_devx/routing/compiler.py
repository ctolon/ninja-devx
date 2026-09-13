"""Turning an operation spec into a Ninja view and registering it on a Ninja ``Router``."""

from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Final,
    Literal,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from ninja import Router
from ninja.constants import NOT_SET, NOT_SET_TYPE
from ninja.throttling import BaseThrottle

from .._internal.compat import signature
from .._internal.generics import defined_in, find_unbound, resolve_annotation, substitute
from .._internal.types import (
    AuthSpec,
    JSONValue,
    MethodFunction,
    ResponseSpec,
    ThrottleSpec,
    ViewFunction,
    status_phrase,
)
from ..configuration.settings import get_settings
from ..dependencies.container import CheckableContainer
from ..exceptions import ControllerConfigError
from ..http.errors import ErrorMap
from ..idempotency.policy import Policy as IdempotencyPolicy
from ..security.permissions import (
    Also,
    AnyPermission,
    requires_async,
)
from .bindings import BindingMarker, ParameterBinding
from .hooks import OperationHook, OperationInfo
from .operations import OPERATIONS_ATTR, OperationSpec

if TYPE_CHECKING:
    from ..dependencies.instances import InstanceProvider
    from .controller import Controller, ControllerOptions
    from .plugins import ControllerPlugin

__all__ = ["Registration", "path_specificity", "throttle_spec"]

from .invocation import Hook, InvocationPlan, make_view


class Registration:
    """Builds the view for one operation spec and registers it on a router."""

    def __init__(
        self,
        cls: type[Controller],
        name: str,
        index: int,
        func: MethodFunction,
        spec: OperationSpec,
        defaults: ControllerOptions,
        instances: InstanceProvider,
        typevars: Mapping[TypeVar, object],
        base: type[Controller],
        plugins: tuple[ControllerPlugin, ...] = (),
    ) -> None:
        self.base = base
        self.cls = cls
        self.name = name
        self.func = func
        self.spec = spec
        self.defaults = defaults
        self.instances = instances
        self.typevars = typevars
        self.plugins = plugins
        self.qualname = f"{cls.__qualname__}.{name}"
        operation_id = spec.options.get("operation_id")
        if operation_id is None:
            # Ninja's default (module + function name) collides when two controllers
            # in one module share a method name.
            suffix = f"_{index + 1}" if index else ""
            operation_id = f"{_snake_case(cls.__name__)}_{name}{suffix}"
        self.operation_id = defaults.get("operation_id_prefix", "") + operation_id
        self.is_async = inspect.iscoroutinefunction(func) or inspect.isasyncgenfunction(func)

    def register(self, router: Router) -> None:
        options = self.spec.options
        view_parameters, bindings = self._parameters()
        permissions = self._permissions()
        hooks: tuple[Hook, ...] = (*self.defaults.get("hooks", ()), *options.get("hooks", ()))
        atomic = options.get("atomic", self.defaults.get("atomic", False))
        errors = self._errors()
        self._validate(permissions, bindings, hooks, atomic, errors)
        policies = [
            decorator
            for decorator in (
                *self.defaults.get("decorators", ()),
                *options.get("decorators", ()),
            )
            if isinstance(decorator, IdempotencyPolicy)
        ]
        if len(policies) > 1:
            raise ControllerConfigError(f"{self.qualname}: only one idempotency policy is allowed")
        if policies and (
            inspect.isgeneratorfunction(self.func) or inspect.isasyncgenfunction(self.func)
        ):
            raise ControllerConfigError(f"{self.qualname}: idempotency does not support streaming")
        if policies:
            from ..security.permission_leaf import checks_objects

            def object_dependent(permission: AnyPermission) -> bool:
                return (
                    any(object_dependent(child) for child in permission.operands)
                    if permission.combinator
                    else checks_objects(permission)
                )

            if (
                any(object_dependent(permission) for permission in permissions)
                and defined_in(self.cls, "authorize_replay") is self.base
            ):
                raise ControllerConfigError(
                    f"{self.qualname}: object permissions need an authorize_replay "
                    "override for idempotency"
                )
            if not self.is_async and inspect.iscoroutinefunction(self.cls.authorize_replay):
                raise ControllerConfigError(
                    f"{self.qualname}: sync idempotency cannot use async authorize_replay"
                )

        operation = OperationInfo(
            controller=self.cls,
            method_name=self.name,
            operation_id=self.operation_id,
            http_methods=self.spec.methods,
            path=self.spec.path,
            is_async=self.is_async,
            metadata=(*self.defaults.get("meta", ()), *options.get("meta", ())),
            database=options.get("database", self.defaults.get("database")),
        )
        plan = InvocationPlan(
            func=self.func,
            operation=operation,
            instances=self.instances,
            permissions=permissions,
            hooks=hooks,
            bindings=bindings,
            atomic=atomic,
            database=options.get("database", self.defaults.get("database")),
            before=defined_in(self.cls, "before_operation") is not self.base,
            after=defined_in(self.cls, "after_operation") is not self.base,
            errors=errors,
            blocking_ms=get_settings().warn_blocking_ms,
            idempotency=policies[0] if policies else None,
        )
        handler = self._decorate(make_view(plan), view_parameters)
        auth = options.get("auth", NOT_SET)
        try:
            router.api_operation(
                list(self.spec.methods),
                self.spec.path,
                auth=auth,
                throttle=throttle_spec(options.get("throttle", NOT_SET)),
                response=self._response(),
                operation_id=self.operation_id,
                summary=options.get("summary"),
                description=options.get("description"),
                tags=options.get("tags"),
                deprecated=options.get("deprecated", self.defaults.get("deprecated")),
                by_alias=options.get("by_alias"),
                exclude_unset=options.get("exclude_unset"),
                exclude_defaults=options.get("exclude_defaults"),
                exclude_none=options.get("exclude_none"),
                url_name=self._url_name(),
                include_in_schema=options.get("include_in_schema", True),
                openapi_extra=self._openapi_extra(
                    auth,
                    permissions,
                    bindings,
                    errors,
                    len(view_parameters) > 1,
                    throttled=options.get("throttle", self.defaults.get("throttle", NOT_SET))
                    is not NOT_SET,
                ),
            )(handler)
            if inspect.isasyncgenfunction(self.func):
                from .._internal.streaming import enable_preflight

                # Ninja normalizes UUID paths while adding them to path_operations.
                for path_view in router.path_operations.values():
                    for registered_operation in path_view.operations:
                        if getattr(registered_operation, "view_func", None) is handler:
                            enable_preflight(registered_operation)
        except Exception as exc:
            exc.add_note(f"while registering {self.qualname} ({self.spec.path!r})")
            raise

    def _url_name(self) -> str | None:
        url_name = self.spec.options.get("url_name")
        prefix = self.defaults.get("url_name_prefix")
        return f"{prefix}_{url_name}" if url_name and prefix else url_name

    def _permissions(self) -> tuple[AnyPermission, ...]:
        declared = self.spec.options.get("permissions")
        inherited = tuple(self.defaults.get("permissions", ()))
        if declared is None:
            return inherited
        if isinstance(declared, Also):
            return (*inherited, *declared)
        return tuple(declared)

    def _errors(self) -> ErrorMap:
        errors = ErrorMap.django_defaults()
        project = get_settings().errors
        if project is not None:
            errors = errors | project
        controller = self.defaults.get("errors")
        if controller is not None:
            errors = errors | controller
        operation = self.spec.options.get("errors")
        if operation is not None:
            errors = errors | operation
        return errors

    # --- Signature ---------------------------------------------------------------

    def _parameters(self) -> tuple[list[inspect.Parameter], tuple[ParameterBinding, ...]]:
        parameters = list(signature(self.func).parameters.values())
        positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        if len(parameters) < 2 or any(p.kind not in positional for p in parameters[:2]):
            raise ControllerConfigError(f"{self.qualname} must accept `self` and `request` first")
        try:
            hints: dict[str, object] = get_type_hints(self.func, include_extras=True)
        except Exception as exc:
            raise ControllerConfigError(
                f"Cannot resolve type annotations of {self.qualname}: {exc}"
            ) from exc

        request = parameters[1].replace(
            kind=inspect.Parameter.POSITIONAL_ONLY,
            annotation=hints.get(parameters[1].name, parameters[1].annotation),
        )
        exposed: list[inspect.Parameter] = [request]
        bindings: list[ParameterBinding] = []
        for parameter in parameters[2:]:
            if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                raise ControllerConfigError(
                    f"{self.qualname}: parameter {parameter.name!r} cannot be positional-only"
                )
            raw = substitute(hints.get(parameter.name, parameter.annotation), self.typevars)
            if (marker := _binding_marker(raw)) is not None:
                binding = marker.bind(parameter, get_args(raw)[0], self.cls, self.spec)
                bindings.append(binding)
                exposed.extend(binding.parameters)
                continue
            annotation = resolve_annotation(raw, self.typevars, self.cls)
            if unbound := find_unbound(annotation):
                raise ControllerConfigError(
                    f"{self.qualname}: parameter {parameter.name!r} uses unbound type variables "
                    f"{unbound}; parameterize the controller's generic base"
                )
            if parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
                # Ninja passes values by name; keyword-only lifts ordering constraints.
                parameter = parameter.replace(kind=inspect.Parameter.KEYWORD_ONLY)
            exposed.append(parameter.replace(annotation=annotation))

        extra = [*self.cls.operation_bindings(self.name, self.spec)]
        for plugin in self.plugins:
            extra.extend(plugin.bindings(self.cls, self.name, self.spec))
        for binding in extra:
            bindings.append(binding)
            exposed.extend(binding.parameters)

        names = [parameter.name for parameter in exposed]
        if duplicates := sorted({name for name in names if names.count(name) > 1}):
            raise ControllerConfigError(f"{self.qualname}: duplicate parameters {duplicates}")
        return exposed, tuple(bindings)

    def _decorate(self, handler: ViewFunction, parameters: list[inspect.Parameter]) -> ViewFunction:
        functools.update_wrapper(handler, self.func)
        attributes: dict[str, object] = handler.__dict__
        del attributes["__wrapped__"]
        attributes.pop(OPERATIONS_ATTR, None)
        # Ninja's attribute-based decorators (csrf_exempt, decorate_view...) append to
        # lists on the function; copy them so handlers never mutate the method.
        for key, value in attributes.items():
            if isinstance(value, list):
                attributes[key] = [*value]
        attributes["__signature__"] = inspect.Signature(parameters)
        attributes["_ninja_devx_idempotency_ready"] = True
        handler.__annotations__ = {
            parameter.name: parameter.annotation
            for parameter in parameters[1:]
            if parameter.annotation is not parameter.empty
        }
        decorators = (
            *self.defaults.get("decorators", ()),
            *self.spec.options.get("decorators", ()),
        )
        for decorator in reversed(decorators):
            handler = decorator(handler)
        return handler

    # --- Validation and documentation ----------------------------------------------

    def _validate(
        self,
        permissions: tuple[AnyPermission, ...],
        bindings: tuple[ParameterBinding, ...],
        hooks: tuple[Hook, ...],
        atomic: bool | Literal["durable"],
        errors: ErrorMap,
    ) -> None:
        is_generator = inspect.isgeneratorfunction(self.func) or inspect.isasyncgenfunction(
            self.func
        )
        container = self.instances.container
        if not self.is_async:
            if not self.instances.supports_sync:
                raise ControllerConfigError(
                    f"{self.qualname} is sync but the container is async-only; make the "
                    "operation async or use a container that also resolves synchronously"
                )
            if any(requires_async(permission) for permission in permissions):
                raise ControllerConfigError(f"{self.qualname} is sync but has async permissions")
            for method in ("before_operation", "after_operation"):
                if inspect.iscoroutinefunction(getattr(self.cls, method)):
                    raise ControllerConfigError(f"{self.qualname} is sync but {method} is async")
            if async_only := [hook for hook in hooks if not isinstance(hook, OperationHook)]:
                raise ControllerConfigError(
                    f"{self.qualname} is sync but hooks {async_only} only implement around_async"
                )
        if atomic and self.is_async:
            raise ControllerConfigError(
                f"{self.qualname}: atomic=True needs a sync operation; in async code wrap the "
                "database work: `await self.run_atomic(function, ...)`"
            )
        if atomic and is_generator:
            raise ControllerConfigError(
                f"{self.qualname}: atomic=True cannot span a streaming response"
            )
        for binding in bindings:
            if binding.check is not None:
                binding.check(container, self.is_async)
        if isinstance(container, CheckableContainer):
            container.check(self.cls, asynchronous=self.is_async)
        for exception in self.spec.options.get("raises", ()):
            if errors.rule_for(exception) is None:
                raise ControllerConfigError(
                    f"{self.qualname}: raises {exception.__qualname__} but no error rule maps "
                    "it; add it to errors=ErrorMap().map(...) or give it http_status and code"
                )

    def _response(self) -> object:
        response: ResponseSpec = self.spec.options.get("response", NOT_SET)
        if isinstance(response, Mapping):
            return {
                code: resolve_annotation(schema, self.typevars, self.cls)
                for code, schema in response.items()
            }
        return resolve_annotation(response, self.typevars, self.cls)

    def _openapi_extra(
        self,
        auth: AuthSpec,
        permissions: tuple[AnyPermission, ...],
        bindings: tuple[ParameterBinding, ...],
        errors: ErrorMap,
        has_parameters: bool,
        *,
        throttled: bool = False,
    ) -> dict[str, JSONValue] | None:
        extra = self.spec.options.get("openapi_extra")
        document = self.spec.options.get(
            "document_errors",
            self.defaults.get("document_errors", get_settings().document_errors),
        )
        if not document:
            return extra

        effective_auth = auth if auth is not NOT_SET else self.defaults.get("auth", NOT_SET)
        codes: set[int] = set(self.cls.documented_errors(self.name, self.spec))
        if effective_auth is not NOT_SET and effective_auth is not None:
            codes.add(401)
        if permissions:
            codes.add(403)
        for binding in bindings:
            codes.update(binding.documented_errors)
        if has_parameters or bindings:
            codes.add(422)
            if set(self.spec.methods) & _BODY_METHODS:
                codes.add(400)  # Ninja's answer to a body it cannot parse
        if throttled:
            codes.add(429)
        for exception in self.spec.options.get("raises", ()):
            rule = errors.rule_for(exception)
            if rule is not None:
                codes.add(rule.status)
        declared = self.spec.options.get("response", NOT_SET)
        if isinstance(declared, Mapping):
            codes -= {code for code in declared if isinstance(code, int)}
        if not codes:
            return extra

        # Ninja keys responses by int status code; matching it merges instead of duplicating.
        responses = {code: _error_response_schema(code) for code in sorted(codes)}
        documented: dict[str, JSONValue] = {"responses": cast("JSONValue", responses)}
        return _deep_merge(documented, extra) if extra else documented


def _detail_schema() -> dict[str, JSONValue]:
    return {
        "type": "object",
        "properties": {"detail": {"type": "string"}, "code": {"type": "string"}},
        "required": ["detail"],
    }


def _validation_schema() -> dict[str, JSONValue]:
    return {
        "type": "object",
        "properties": {
            "detail": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "loc": {
                            "type": "array",
                            "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                        },
                        "msg": {"type": "string"},
                    },
                    "required": ["type", "loc", "msg"],
                },
            }
        },
        "required": ["detail"],
    }


_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH"})


def _error_response_schema(code: int) -> JSONValue:
    schema = _validation_schema() if code == 422 else _detail_schema()
    return {
        "description": status_phrase(code),
        "content": {"application/json": {"schema": schema}},
    }


def _deep_merge(
    base: dict[str, JSONValue], override: Mapping[str, JSONValue]
) -> dict[str, JSONValue]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _binding_marker(annotation: object) -> BindingMarker | None:
    if get_origin(annotation) is None:
        return None
    for item in getattr(annotation, "__metadata__", ()):
        if isinstance(item, BindingMarker):
            return item
    return None


# --- Views -----------------------------------------------------------------------


_CONVERTER = re.compile(r"^\{(?:(\w+):)?\w+\}$")


def path_specificity(path: str) -> tuple[int, ...]:
    """Sort key per segment: static (0) < typed converter ``{int:pk}`` (1) < ``{name}`` (2)."""
    ranks: list[int] = []
    for segment in path.strip("/").split("/"):
        match = _CONVERTER.match(segment)
        if match is None:
            ranks.append(0)
        else:
            ranks.append(1 if match.group(1) not in (None, "str", "path") else 2)
    return tuple(ranks)


def throttle_spec(throttle: ThrottleSpec) -> BaseThrottle | list[BaseThrottle] | NOT_SET_TYPE:
    if isinstance(throttle, BaseThrottle | NOT_SET_TYPE):
        return throttle
    return list(throttle)


def _snake_case(name: str) -> str:
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
