"""Shared session metadata for standalone Ralph MCP processes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.protocol.capability_mapping import lookup_ralph_capability
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV

if TYPE_CHECKING:
    from pathlib import Path


def _normalize_capability_token(value: str) -> str:
    return value.strip().replace("-", "_").replace(".", "_").lower()


def session_has_capability(granted: set[str], requested: str) -> bool:
    """Return True if the requested capability is present in the granted set."""
    normalized_granted = set[str]()
    for value in granted:
        normalized_granted.add(_normalize_capability_token(value))
        mapped_granted = lookup_ralph_capability(value)
        if mapped_granted is not None:
            normalized_granted.add(_normalize_capability_token(mapped_granted.value))

    candidates = {_normalize_capability_token(requested)}
    mapped = lookup_ralph_capability(requested)
    if mapped is not None:
        candidates.add(_normalize_capability_token(mapped.value))
    if requested in {"WorkspaceWriteAny", "FileWrite"}:
        candidates.update({"workspace_write_ephemeral", "workspace_write_tracked"})
    return any(candidate in normalized_granted for candidate in candidates)


@dataclass
class AgentSession:
    """Lightweight session holder used by standalone Ralph MCP tooling."""

    session_id: str
    run_id: str
    drain: str
    capabilities: set[str] = field(default_factory=set)
    policy_flags: set[str] | None = None
    created_at: float = field(default_factory=time.time)
    parallel_worker: bool = False
    edit_area_result: object = None
    worker_artifact_dir: Path | None = None
    worker_namespace: Path | None = None
    media_manifest: MediaManifest = field(default_factory=MediaManifest)
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

    def check_capability(self, capability: str) -> object:
        return "approved" if session_has_capability(self.capabilities, capability) else "denied"

    def is_parallel_worker(self) -> bool:
        return self.parallel_worker

    def check_edit_area(self, _: str) -> object:
        return self.edit_area_result if self.edit_area_result is not None else "approved"


__all__ = [
    "MCP_ENDPOINT_ENV",
    "MCP_RUN_ID_ENV",
    "AgentSession",
    "MediaManifest",
    "session_has_capability",
]
