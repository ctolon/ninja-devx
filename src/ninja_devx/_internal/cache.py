"""Class-owned caches that cannot accidentally inherit a parent's resolved values."""

from typing import TypeVar, cast

K = TypeVar("K")
V = TypeVar("V")


def owned_cache(owner: type[object], name: str) -> dict[K, V]:  # pyright: ignore[reportInvalidTypeVarUse]
    """Keep generated values with their source class, including self-referential types."""
    attribute = f"__devx_cache_{name}"
    cache = vars(owner).get(attribute)
    if cache is None:
        cache = {}
        setattr(owner, attribute, cache)
    return cast("dict[K, V]", cache)
