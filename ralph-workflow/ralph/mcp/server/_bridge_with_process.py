"""Protocol for a session bridge that exposes its process."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.mcp.protocol._session_bridge_like import SessionBridgeLike

if TYPE_CHECKING:
    from ralph.mcp.server._process_with_pid import _ProcessWithPid


@runtime_checkable
class _BridgeWithProcess(SessionBridgeLike, Protocol):
    process: _ProcessWithPid
