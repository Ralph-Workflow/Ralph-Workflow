"""MCP bridge - compatibility wrappers over the artifact sub-package."""

from __future__ import annotations

from ralph.mcp.artifacts import bridge as _impl
from ralph.mcp.artifacts.bridge import (
    DEFAULT_BRIDGE_ARTIFACT_DEPS,
    BridgeArtifactDeps,
    BridgeConfig,
    BridgeError,
    MCPTool,
)
from ralph.mcp.artifacts.store import (
    ArtifactExistsError,
    ArtifactNotFoundError,
    ArtifactSubmitOptions,
    get_artifact,
    list_artifacts,
    submit_artifact,
)


class MCPBridge(_impl.MCPBridge):
    def submit_artifact_mcp(
        self,
        name: str,
        artifact_type: str,
        content: dict[str, object],
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            artifact = submit_artifact(
                self._config.artifact_dir,
                name,
                artifact_type,
                content,
                ArtifactSubmitOptions(
                    metadata=metadata,
                    persistence=self._config.artifact_deps.persistence,
                ),
            )
            return {"success": True, "artifact": artifact.to_dict()}
        except ArtifactExistsError as exc:
            return {"success": False, "error": str(exc)}

    def get_artifact_mcp(self, name: str) -> dict[str, object]:
        try:
            artifact = get_artifact(
                self._config.artifact_dir,
                name,
                backend=self._config.artifact_deps.backend,
            )
            return {"success": True, "artifact": artifact.to_dict()}
        except ArtifactNotFoundError as exc:
            return {"success": False, "error": str(exc)}

    def list_artifacts_mcp(self) -> dict[str, object]:
        artifacts = list_artifacts(
            self._config.artifact_dir,
            backend=self._config.artifact_deps.backend,
        )
        return {
            "success": True,
            "artifacts": [artifact.to_dict() for artifact in artifacts],
        }


__all__ = [
    "DEFAULT_BRIDGE_ARTIFACT_DEPS",
    "BridgeArtifactDeps",
    "BridgeConfig",
    "BridgeError",
    "MCPBridge",
    "MCPTool",
    "get_artifact",
    "list_artifacts",
    "submit_artifact",
]
