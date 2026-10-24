"""ExitHandler protocol for the bounded pre-serve path.

The class lives in its own module so ``_pre_serve_overrun`` conforms to the
repo-structure policy (at most one public top-level class per module). It is
re-exported from :mod:`ralph.mcp.server._pre_serve_overrun`, so every existing
importer keeps working unchanged.
"""

from __future__ import annotations

from typing import Protocol


class ExitHandler(Protocol):
    """Callable invoked to terminate execution when an overrun forces process exit."""

    def __call__(self, exit_code: int, /) -> object: ...


__all__ = ["ExitHandler"]
