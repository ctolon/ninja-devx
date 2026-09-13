"""Cosmic Python, lite: commands, use case handlers and repository ports.

Handlers depend on ``Repository[...]`` protocols and a ``RequestContext`` instead of
Django or HTTP, so they are unit-tested with ``InMemoryRepository`` and no database.
The container wires the Django implementations for the API.
"""
