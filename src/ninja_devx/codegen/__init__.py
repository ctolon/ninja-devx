"""Typed API clients generated from a Django Ninja OpenAPI schema."""

from .openapi import Operation, Parameter, load_api, read_operations
from .python import generate_python
from .typescript import generate_typescript

__all__ = [
    "Operation",
    "Parameter",
    "generate_python",
    "generate_typescript",
    "load_api",
    "read_operations",
]
