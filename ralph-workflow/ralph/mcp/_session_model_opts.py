"""Optional model resolution parameters for build_session_mcp_plan."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity


@dataclass(frozen=True)
class SessionModelOpts:
    """Optional model resolution parameters for build_session_mcp_plan."""

    model_identity: MultimodalModelIdentity | None = None
    model_flag: str | None = None
