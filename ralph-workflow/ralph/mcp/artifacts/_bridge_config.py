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
    """Configuration bundle handed to :class:`ralph.mcp.artifacts.bridge.MCPBridge`.

    A :class:`BridgeConfig` is the immutable-ish value object that callers
    construct once per pipeline run (or per standalone MCP session) and then
    pass into :class:`MCPBridge` so the bridge knows where artifacts live,
    which transport to expose, which dependency seams to use, and which
    ``run_id`` to stamp on every artifact it routes. The dataclass is
    mutable by default so callers can adjust fields after construction in
    tests; production callers should treat an instance as effectively
    read-only once it has been handed to a bridge.

    Attributes:
        artifact_dir: Directory (relative or absolute) where the bridge
            reads and writes artifact files. Defaults to
            ``.agent/artifacts`` to match the conventional Ralph workspace
            layout. The bridge resolves the path against ``workspace_root``
            when it is relative, so callers should set both consistently.
        workspace_root: Workspace root the bridge treats as the user's
            project boundary. Tools that ask the bridge for the workspace
            see this value; artifact paths are usually resolved under it.
        transport: Optional :class:`ralph.mcp.protocol.transport.MCPTransport`
            (e.g. :class:`StdioTransport`, in-memory transports for tests).
            When ``None`` the bridge picks a transport at start time based
            on its environment and command-line wiring.
        artifact_deps: Dependency bundle controlling how the bridge creates
            and reads artifacts. Defaults to the production
            :class:`BridgeArtifactDeps` instance; tests can swap in a stub
            to avoid touching the filesystem.
        run_id: Stable identifier the bridge stamps onto every artifact it
            produces during this run. Surfaces in the artifact store index
            and in log lines so a single multi-bridge pipeline run can be
            traced end-to-end. The default ``"mcp-bridge"`` is appropriate
            for standalone use; pipelines should override it with their
            per-run identifier.

    Invariants:
        - The bridge assumes ``artifact_dir`` and ``workspace_root`` were
          set by a trusted caller; passing user-supplied paths directly
          would cross the trust boundary between the bridge and the
          agent-facing tool surface.
        - ``run_id`` is propagated to artifacts but is not used for
          authorization decisions; it is a tracing affordance only.
    """

    artifact_dir: Path = Path(".agent/artifacts")
    workspace_root: Path = Path()
    transport: MCPTransport | None = None
    artifact_deps: BridgeArtifactDeps = field(default_factory=BridgeArtifactDeps)
    run_id: str = "mcp-bridge"


__all__ = ["BridgeConfig"]
