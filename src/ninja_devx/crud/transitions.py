"""State transitions exposed as API operations, with permissions, guards and a listing route.

Declare the allowed moves; each becomes ``POST /{pk}/<name>``::

    class ArticleController(TransitionsMixin[Article, ArticleOut], CRUDController[...]):
        state_field = "status"
        transitions = {
            "publish": Transition(source=("draft",), target="published", permissions=[IsStaff()]),
            "archive": Transition(source=("published",), target="archived"),
        }

``GET /{pk}/transitions`` lists the names allowed from the object's current state for the
caller (permissions and guards evaluated, without performing anything). Rename or disable
routes with ``routes`` (operation names are ``transition_<name>`` and ``transitions_list``).

This stays API-level: it does not integrate with model-level FSMs (``django-fsm-2`` and
similar). Call a ``@transition``-decorated model method from ``Transition.on_transition``
or ``on_transition()`` if a project already has one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Generic, Unpack, cast

from django.db.models import CharField, Model
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import Router

from .._internal.cache import owned_cache
from .._permission_eval import denied
from .._permission_eval_async import adenied
from ..dependencies.container import ContainerLike
from ..dependencies.instances import Scope
from ..exceptions import ControllerConfigError
from ..layers.errors import Conflict
from ..routing.controller import ControllerOptions
from ..routing.operations import async_variant, get, get_operation_specs, post
from ..security.permissions import Also, AnyPermission
from .annotations import Lookup
from .controllers import OUT_SCHEMA, ModelController, ModelT, OutT
from .fields import resolve_field
from .writes import write_scope

__all__ = ["InvalidTransition", "Transition", "TransitionsMixin"]


class InvalidTransition(Conflict):
    """The object's current state is not a source state for this transition."""

    code = "invalid_transition"


@dataclass(frozen=True, slots=True)
class Transition:
    """One named move from a set of source states to a target state."""

    source: Sequence[str]
    """States the object must be in for this transition to apply."""
    target: str
    """State written when the transition succeeds."""
    permissions: Sequence[AnyPermission] = ()
    """Added to the controller's permissions for this transition's route only."""
    guard: Callable[[HttpRequest, Model], bool] | None = None
    """Extra precondition beyond the source state; ``False`` also answers 409."""
    on_transition: Callable[[HttpRequest, Model], None] | None = None
    """Called after the new state is saved, inside the write transaction."""


class TransitionsMixin(ModelController[ModelT], Generic[ModelT, OutT]):
    """Adds ``POST /{pk}/<name>`` for each declared transition and ``GET /{pk}/transitions``."""

    state_field: ClassVar[str] = "status"
    """The ``CharField`` holding the object's state."""
    transitions: ClassVar[Mapping[str, Transition]] = MappingProxyType({})
    """Transition name to :class:`Transition`."""

    def current_state(self, instance: ModelT) -> str:
        return cast("str", getattr(instance, type(self).state_field))

    def transition_config(self, name: str) -> Transition:
        return type(self).transitions[name]

    def transition_allowed(
        self, request: HttpRequest, instance: ModelT, config: Transition
    ) -> bool:
        """Whether ``instance``'s current state and ``config.guard`` allow the transition.

        Does not check permissions; see ``allowed_transitions`` for that.
        """
        return self.current_state(instance) in config.source and (
            config.guard is None or config.guard(request, instance)
        )

    def perform_transition(self, request: HttpRequest, instance: ModelT, name: str) -> ModelT:
        """Write ``config.target`` to ``state_field`` and run the transition hooks.

        Raises ``InvalidTransition`` (409) when the current state or the guard refuses.
        """
        config = self.transition_config(name)
        if not self.transition_allowed(request, instance, config):
            raise InvalidTransition(
                _("Cannot %(name)s from %(state)r.")
                % {"name": name, "state": self.current_state(instance)}
            )
        field = type(self).state_field
        setattr(instance, field, config.target)
        instance.save(update_fields=[field])
        if config.on_transition is not None:
            config.on_transition(request, instance)
        self.on_transition(request, instance, name)
        return instance

    def on_transition(self, request: HttpRequest, instance: ModelT, name: str) -> None:
        """Called after any transition, after ``Transition.on_transition``. No-op by default."""

    def run_transition(self, request: HttpRequest, lookup: object, name: str) -> ModelT:
        with write_scope(self, request):
            instance = self.get_object(request, lookup, lock=True)
            updated = self.perform_transition(request, instance, name)
            return self.refresh(request, updated)

    async def arun_transition(self, request: HttpRequest, lookup: object, name: str) -> ModelT:
        await self.aprepare_request(request)
        return await self.run_sync(self.run_transition, request, lookup, name)

    def allowed_transitions(self, request: HttpRequest, instance: ModelT) -> list[str]:
        """Names of the transitions ``instance``'s state, guards and permissions allow."""
        base = type(self).merged_options().get("permissions", ())
        names: list[str] = []
        for name, config in type(self).transitions.items():
            if not self.transition_allowed(request, instance, config):
                continue
            combined: Sequence[AnyPermission] = (*base, *config.permissions)
            if all(denied(permission, request, (instance,)) is None for permission in combined):
                names.append(name)
        return names

    async def aallowed_transitions(self, request: HttpRequest, instance: ModelT) -> list[str]:
        base = type(self).merged_options().get("permissions", ())
        names: list[str] = []
        for name, config in type(self).transitions.items():
            if not self.transition_allowed(request, instance, config):
                continue
            combined: Sequence[AnyPermission] = (*base, *config.permissions)
            allowed = True
            for permission in combined:
                if await adenied(permission, request, (instance,)) is not None:
                    allowed = False
                    break
            if allowed:
                names.append(name)
        return names

    @get("/{pk}/transitions", response=list[str])
    def transitions_list(self, request: HttpRequest, pk: Lookup) -> list[str]:
        return self.allowed_transitions(request, self.get_object(request, pk))

    @async_variant(transitions_list)
    async def atransitions_list(self, request: HttpRequest, pk: Lookup) -> list[str]:
        return await self.aallowed_transitions(request, await self.aget_object(request, pk))

    @classmethod
    def as_router(
        cls,
        *,
        container: ContainerLike | None = None,
        scope: Scope | None = None,
        **options: Unpack[ControllerOptions],
    ) -> Router:
        cls._install_transitions()
        return super().as_router(container=container, scope=scope, **options)

    @classmethod
    def _install_transitions(cls) -> None:
        installed: dict[type[object], bool] = owned_cache(cls, "transitions_installed")
        if installed.get(cls):
            return
        installed[cls] = True
        transitions = cls.transitions
        if not transitions:
            return
        model = cls.get_model()
        field = resolve_field(model, cls.state_field)
        if not isinstance(field, CharField):
            raise ControllerConfigError(
                f"{cls.__qualname__}.state_field {cls.state_field!r} must be a CharField, "
                f"got {type(field).__name__}"
            )
        choices = {value for value, _label in field.flatchoices}
        reserved = _existing_paths(cls)
        for name, config in transitions.items():
            if choices and (invalid := sorted({*config.source, config.target} - choices)):
                raise ControllerConfigError(
                    f"{cls.__qualname__}.transitions[{name!r}]: {invalid} not in "
                    f"{model.__name__}.{cls.state_field} choices"
                )
            path = f"/{{pk}}/{name}"
            if path in reserved:
                raise ControllerConfigError(
                    f"{cls.__qualname__}.transitions[{name!r}] collides with an existing "
                    f"route at {path!r}"
                )
            reserved.add(path)
            sync_op, async_op = _build_operations(name, config)
            setattr(cls, f"transition_{name}", sync_op)
            setattr(cls, f"atransition_{name}", async_op)


def _existing_paths(cls: type[object]) -> set[str]:
    paths: set[str] = set()
    for klass in cls.__mro__:
        for member in vars(klass).values():
            target = getattr(member, "__func__", member)
            for spec in get_operation_specs(target):
                paths.add(spec.path)
    return paths


def _build_operations(
    name: str, config: Transition
) -> tuple[Callable[..., object], Callable[..., object]]:
    def sync_op(self: TransitionsMixin[ModelT, OutT], request: HttpRequest, pk: Lookup) -> ModelT:
        return self.run_transition(request, pk, name)

    async def async_op(
        self: TransitionsMixin[ModelT, OutT], request: HttpRequest, pk: Lookup
    ) -> ModelT:
        return await self.arun_transition(request, pk, name)

    sync_op.__name__ = sync_op.__qualname__ = f"transition_{name}"
    async_op.__name__ = async_op.__qualname__ = f"atransition_{name}"
    decorated = post(
        f"/{{pk}}/{name}",
        response=OUT_SCHEMA,
        permissions=Also(*config.permissions),
        raises=(InvalidTransition,),
    )(sync_op)
    async_variant(decorated)(async_op)
    return decorated, async_op
