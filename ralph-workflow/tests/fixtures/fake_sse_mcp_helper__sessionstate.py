"""Private helper: _SessionState for fake SSE MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import queue


@dataclass
class _SessionState:
    """Holds per-session state for the fake SSE MCP server."""

    events: queue.Queue[bytes]
