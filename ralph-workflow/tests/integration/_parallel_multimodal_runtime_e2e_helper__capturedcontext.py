from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity


class _CapturedContext:
    """Holds captured session contract values from the coordinator's run_fan_out call."""

    def __init__(self) -> None:
        self.session_drain: str | None = None
        self.session_capabilities: frozenset[str] | None = None
        self.session_model_identity: MultimodalModelIdentity | None = None
        self.session_capability_profile: object | None = None
