
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    caller_identity_for,
    caller_profile_for,
    resolve_capability_profile,
)
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.protocol.capability_mapping import lookup_ralph_capability
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.tools._exec_resource_protocol import ExecResourceResolverLike


def _normalize_capability_token(value: str) -> str:
    return value.strip().replace("-", "_").replace(".", "_").lower()


def session_has_capability(granted: set[str], requested: str) -> bool:
    normalized_granted = set[str]()
    for value in granted:
        normalized_granted.add(_normalize_capability_token(value))
        mapped_granted = lookup_ralph_capability(value)
        if mapped_granted is not None:
            normalized_granted.add(_normalize_capability_token(mapped_granted.value))
        if value in {"WorkspaceWriteAny", "FileWrite"}:
            normalized_granted.update({"workspace_write_ephemeral", "workspace_write_tracked"})

    candidates = {_normalize_capability_token(requested)}
    mapped = lookup_ralph_capability(requested)
    if mapped is not None:
        candidates.add(_normalize_capability_token(mapped.value))
    if requested in {"WorkspaceWriteAny", "FileWrite"}:
        candidates.update({"workspace_write_ephemeral", "workspace_write_tracked"})
    return any(candidate in normalized_granted for candidate in candidates)


ToolOutputSinkEntry = tuple[int | None, "Callable[[dict[str, object]], None]"]


class McpSession(Protocol):

    tool_output_sink_entry: ToolOutputSinkEntry | None

    @property
    def session_id(self) -> str:
        ...

    @property
    def run_id(self) -> str:
        ...

    @property
    def broker_secret(self) -> str | None:
        ...

    @property
    def drain(self) -> str:
        ...

    @property
    def activity_only_supervision(self) -> bool:
        ...

    @property
    def capabilities(self) -> set[str]:
        ...

    @property
    def policy_flags(self) -> set[str] | None:
        ...

    @property
    def created_at(self) -> float:
        ...

    @property
    def parallel_worker(self) -> bool:
        ...

    @property
    def edit_area_result(self) -> object:
        ...

    @property
    def worker_artifact_dir(self) -> Path | None:
        ...

    @property
    def worker_namespace(self) -> Path | None:
        ...

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        ...

    @property
    def media_manifest(self) -> MediaManifest:
        ...

    @property
    def model_identity(self) -> MultimodalModelIdentity:
        ...

    @property
    def stored_capability_profile(self) -> ResolvedCapabilityProfile | None:
        ...

    @property
    def capability_profile(self) -> ResolvedCapabilityProfile | None:
        ...

    @property
    def delegated_agent_id(self) -> str | None:
        ...

    @property
    def delegated_model_identity(self) -> MultimodalModelIdentity | None:
        ...

    @property
    def delegated_capability_profile(self) -> ResolvedCapabilityProfile | None:
        ...

    @property
    def caller_agent_id(self) -> str:
        ...

    @property
    def caller_model_identity(self) -> MultimodalModelIdentity:
        ...

    @property
    def caller_capability_profile(self) -> ResolvedCapabilityProfile:
        ...

    def check_capability(self, capability: str, /) -> object:
        ...

    def is_parallel_worker(self) -> bool:
        ...

    def check_edit_area(self, path: str, /) -> object:
        ...

    def current_thread_tool_output_sink(
        self,
    ) -> Callable[[dict[str, object]], None] | None:
        ...

    @property
    def explore_index(self) -> object | None:
        ...

    @property
    def exec_resource_resolver(self) -> ExecResourceResolverLike | None:
        ...


@dataclass
class AgentSession:

    session_id: str
    run_id: str
    drain: str
    capabilities: set[str] = field(default_factory=set)
    activity_only_supervision: bool = False
    policy_flags: set[str] | None = None
    created_at: float = field(default_factory=time.time)
    parallel_worker: bool = False
    edit_area_result: object = None
    worker_artifact_dir: Path | None = None
    worker_namespace: Path | None = None
    allowed_roots: tuple[Path, ...] = field(default_factory=tuple)
    media_manifest: MediaManifest = field(default_factory=MediaManifest)
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)
    stored_capability_profile: ResolvedCapabilityProfile | None = field(default=None)
    delegated_agent_id: str | None = field(default=None)
    delegated_model_identity: MultimodalModelIdentity | None = field(default=None)
    delegated_capability_profile: ResolvedCapabilityProfile | None = field(default=None)
    broker_secret: str | None = field(default=None, repr=False)
    tool_output_sink_entry: ToolOutputSinkEntry | None = field(default=None, repr=False)
    explore_index: object | None = field(default=None, repr=False)
    exec_resource_resolver: ExecResourceResolverLike | None = field(default=None, repr=False)

    @property
    def capability_profile(self) -> ResolvedCapabilityProfile:
        if self.stored_capability_profile is not None:
            return self.stored_capability_profile
        return resolve_capability_profile(self.model_identity)

    @property
    def caller_agent_id(self) -> str:
        return self.delegated_agent_id or self.session_id

    @property
    def caller_model_identity(self) -> MultimodalModelIdentity:
        return caller_identity_for(self.model_identity, self.delegated_model_identity)

    @property
    def caller_capability_profile(self) -> ResolvedCapabilityProfile:
        return caller_profile_for(
            self.model_identity,
            self.delegated_model_identity,
            self.capability_profile,
            self.delegated_capability_profile,
        )

    def check_capability(self, capability: str) -> object:
        return "approved" if session_has_capability(self.capabilities, capability) else "denied"

    def is_parallel_worker(self) -> bool:
        return self.parallel_worker

    def check_edit_area(self, _: str) -> object:
        return self.edit_area_result if self.edit_area_result is not None else "approved"

    def current_thread_tool_output_sink(self) -> Callable[[dict[str, object]], None] | None:
        entry = self.tool_output_sink_entry
        if entry is None:
            return None
        owner, sink = entry
        if owner is None or owner == threading.get_ident():
            return sink
        return None


__all__ = [
    "MCP_ENDPOINT_ENV",
    "MCP_RUN_ID_ENV",
    "AgentSession",
    "McpSession",
    "MediaManifest",
    "ToolOutputSinkEntry",
    "session_has_capability",
]
