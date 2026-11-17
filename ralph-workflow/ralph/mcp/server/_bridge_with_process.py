from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.mcp.server._process_with_pid import _ProcessWithPid


@runtime_checkable
class _BridgeWithProcess(Protocol):
    process: _ProcessWithPid

    @property
    def run_id(self) -> str: ...

    def start(self) -> None: ...

    def agent_endpoint_uri(self) -> str: ...

    def endpoint_uri(self) -> str: ...

    def shutdown(self) -> None: ...
