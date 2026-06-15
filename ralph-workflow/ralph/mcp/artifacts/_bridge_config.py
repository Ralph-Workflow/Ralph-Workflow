"""BridgeConfig — configuration for the MCP bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts._bridge_artifact_deps import BridgeArtifactDeps

if TYPE_CHECKING:
    from ralph.mcp.protocol.transport import MCPTransport


@dataclass
class BridgeConfig:
    """Configuration for MCP bridge."""

    artifact_dir: Path = Path(".agent/artifacts")
    workspace_root: Path = Path()
    transport: MCPTransport | None = None
    artifact_deps: BridgeArtifactDeps = field(default_factory=BridgeArtifactDeps)
    run_id: str = "mcp-bridge"


__all__ = ["BridgeConfig"]
