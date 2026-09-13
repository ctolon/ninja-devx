"""Stable command entry point; implementation lives in tooling.unasync."""

from .tooling.unasync import main

if __name__ == "__main__":
    raise SystemExit(main())
