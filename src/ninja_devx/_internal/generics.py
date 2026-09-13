"""Resolve generic type parameters of controller classes at registration time.

``class ArticleController(CRUDController[Article, ArticleOut, ArticleIn])`` binds the
TypeVars used in inherited method annotations and operation options; handlers are
built with the concrete types so Ninja sees ``payload: ArticleIn``.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Generic, TypeVar, cast, get_args, get_origin

from ..exceptions import ControllerConfigError
from .cache import owned_cache

__all__ = ["LazyAnnotation", "defined_in", "resolve_annotation", "substitute", "type_arguments"]


class LazyAnnotation:
    """``Annotated`` metadata replaced by a concrete annotation when a controller is built.

    ``Annotated[T, marker]`` becomes ``marker.resolve(T, controller)``.
    """

    def resolve(self, annotation: object, controller: type[object]) -> object:
        raise NotImplementedError


def type_arguments(cls: type[object]) -> Mapping[TypeVar, object]:
    """Map every TypeVar bound anywhere in ``cls``'s generic ancestry to its argument.

    Cached per class: controllers and services call this on every request.
    """
    _type_arguments: dict[type[object], Mapping[TypeVar, object]] = owned_cache(
        cls, "generics_type_arguments"
    )
    cached = _type_arguments.get(cls)
    if cached is None:
        computed = _compute_type_arguments(cls)
        # A class may derive some arguments from others (``AutoCRUDController[Post]``
        # generates its schemas from the model).
        derive: object = getattr(cls, "derive_type_arguments", None)
        if callable(derive):
            extra = cast("Mapping[TypeVar, object]", derive(MappingProxyType(computed)))
            computed.update(extra)
        cached = _type_arguments[cls] = MappingProxyType(computed)
    return cached


def _compute_type_arguments(cls: type[object]) -> dict[TypeVar, object]:
    mapping: dict[TypeVar, object] = {}
    # Subclasses come first in the MRO, so their bindings are known before a base
    # re-parameterizes its own bases with the same TypeVars.
    for klass in cls.__mro__:
        bases: tuple[object, ...] = klass.__dict__.get("__orig_bases__", ())
        for base in bases:
            origin = get_origin(base)
            if origin is None or origin is Generic:
                continue
            parameters: tuple[object, ...] = getattr(origin, "__parameters__", ())
            for parameter, argument in zip(parameters, get_args(base), strict=False):
                if not isinstance(parameter, TypeVar):
                    continue
                value = substitute(argument, mapping)
                if value is parameter:
                    continue
                existing = mapping.setdefault(parameter, value)
                if existing != value and not isinstance(value, TypeVar):
                    raise ControllerConfigError(
                        f"{cls.__qualname__}: conflicting arguments for {parameter}: "
                        f"{existing!r} and {value!r}"
                    )
    return mapping


def substitute(annotation: object, mapping: Mapping[TypeVar, object]) -> object:
    """Replace TypeVars in ``annotation`` (``T``, ``list[T]``, ``Annotated[T, ...]``...)."""
    if isinstance(annotation, TypeVar):
        return mapping.get(annotation, annotation)
    parameters: tuple[object, ...] = getattr(annotation, "__parameters__", ())
    if not parameters or not any(parameter in mapping for parameter in parameters):
        return annotation
    arguments = tuple(
        mapping.get(parameter, parameter) if isinstance(parameter, TypeVar) else parameter
        for parameter in parameters
    )
    try:
        return annotation[arguments]  # type: ignore[index]  # pyright: ignore[reportIndexIssue, reportUnknownVariableType]
    except TypeError as exc:
        raise ControllerConfigError(f"Cannot substitute type arguments in {annotation!r}") from exc


def resolve_annotation(
    annotation: object, mapping: Mapping[TypeVar, object], controller: type[object]
) -> object:
    """Substitute TypeVars, then expand ``LazyAnnotation`` markers."""
    annotation = substitute(annotation, mapping)
    if get_origin(annotation) is not Annotated:
        return annotation
    base, *metadata = get_args(annotation)
    for index, item in enumerate(metadata):
        if isinstance(item, LazyAnnotation):
            resolved = item.resolve(base, controller)
            rest = [*metadata[:index], *metadata[index + 1 :]]
            return Annotated[resolved, *rest] if rest else resolved
    return annotation


def find_unbound(annotation: object) -> tuple[TypeVar, ...]:
    if isinstance(annotation, TypeVar):
        return (annotation,)
    parameters: tuple[object, ...] = getattr(annotation, "__parameters__", ())
    return tuple(parameter for parameter in parameters if isinstance(parameter, TypeVar))


def defined_in(
    cls: type[object], attribute: str, *, through_wrappers: bool = False
) -> type[object] | None:
    """The class in ``cls``'s MRO that defines ``attribute``.

    With ``through_wrappers``, classes listing ``attribute`` in their own
    ``__devx_wraps__`` (mixins that only add behaviour around ``super()``) are skipped.
    """
    for klass in cls.__mro__:
        if attribute not in vars(klass):
            continue
        wraps: object = vars(klass).get("__devx_wraps__", ())
        if through_wrappers and isinstance(wraps, frozenset) and attribute in wraps:
            continue
        return klass
    return None
