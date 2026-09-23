class NinjaDevXError(Exception):
    """Base class for all ninja-devx errors."""


class ControllerConfigError(NinjaDevXError):
    """A controller or one of its operations is declared incorrectly."""


class DependencyResolutionError(NinjaDevXError):
    """A dependency could not be resolved by the container."""


class CircularDependencyError(DependencyResolutionError):
    """The dependency graph contains a cycle."""


class AsyncLazyAccessError(NinjaDevXError):
    """Sync-only Django code (usually a lazy relation) ran inside an async operation."""

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation} touched the database synchronously inside an async operation. "
            "Load relations up front (select_related/prefetch_related, or add them to the "
            "output schema so ModelController optimizes the queryset), use the a-prefixed "
            "ORM methods, or run the code with `await self.run_sync(...)`."
        )


class BlockingCallWarning(RuntimeWarning):
    """Sync code blocked an async operation's event loop (see NINJA_DEVX["WARN_BLOCKING_MS"])."""


class AsyncDatabaseTestWarning(UserWarning):
    """An async test uses the database without ``django_db(transaction=True)``.

    ``sync_to_async`` runs ORM calls on another thread with its own connection, outside
    the test's transaction: rows leak between tests or are invisible to the view.
    """


class MixedPathWarning(UserWarning):
    """One path is served by both sync and async operations (costs a thread hop per call)."""


class NinjaDevXDeprecationWarning(DeprecationWarning):
    """A ninja-devx API scheduled for removal; the message names the replacement.

    ``filterwarnings = ["error::ninja_devx.NinjaDevXDeprecationWarning"]`` turns every use
    into a test failure before upgrading.
    """
