"""Protocol for the exec resource resolver attached to MCP sessions.

The protocol keeps :mod:`ralph.mcp.protocol.session` decoupled from
the concrete :class:`ralph.mcp.tools._exec_resource_uri.ExecResourceResolver`
implementation. The concrete class is the only registered
implementation, but other tools can satisfy the contract for tests
without pulling in the full exec-resource surface.

The duck-typed surface is the minimum the MCP server relies on:

* ``read(uri)`` returns ``tuple[bytes, str, int] | None``.
* ``list_entries()`` returns an iterable of objects with
  ``resource_list_entry()`` returning a ``dict[str, object]``.

A missing resolver (``None``) is treated the same as an unknown
artifact: the MCP server reports a structured "resolver not
attached" error so legacy clients get a consistent failure mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class ExecResourceEntryLike(Protocol):
    """Protocol for objects returned by ``ExecResourceResolver.list_entries``."""

    def resource_list_entry(self) -> dict[str, object]:
        """Return the JSON-friendly resource entry shape."""
        ...


class ExecResourceResolverLike(Protocol):
    """Protocol for the resolver attached to a session.

    Both :class:`ralph.mcp.tools._exec_resource_uri.ExecResourceResolver`
    and the test doubles used in
    ``tests/test_tool_exec_resource_uri.py`` satisfy this contract.
    """

    def read(self, uri: str) -> tuple[bytes, str, int] | None:
        """Return ``(bytes, mime_type, total_size)`` for ``uri`` or ``None``."""
        ...

    def list_entries(self) -> Iterable[ExecResourceEntryLike]:
        """Return the registered spill entries."""
        ...

    def register(self, spill_path: Path) -> str:
        """Register a spill file and return its replayable URI.

        The concrete :class:`ralph.mcp.tools._exec_resource_uri.ExecResourceResolver`
        declares ``register(self, spill_path: Path)``. The protocol
        matches that signature so the structural subtyping check
        passes for the production class.
        """
        ...


__all__ = [
    "ExecResourceEntryLike",
    "ExecResourceResolverLike",
]
