import inspect
import sys
from collections.abc import Callable

__all__ = ["signature"]


if sys.version_info >= (3, 14):  # pragma: no cover - version specific
    from annotationlib import Format

    def signature(obj: Callable[..., object]) -> inspect.Signature:
        """``inspect.signature`` that tolerates annotations referring to undefined names."""
        return inspect.signature(obj, annotation_format=Format.FORWARDREF)

else:  # pragma: no cover - version specific

    def signature(obj: Callable[..., object]) -> inspect.Signature:
        """``inspect.signature`` (annotations are never evaluated before Python 3.14)."""
        return inspect.signature(obj)
