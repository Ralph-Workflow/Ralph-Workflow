"""Shared session metadata for standalone Ralph MCP processes."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.protocol.capability_mapping import lookup_ralph_capability
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV

if TYPE_CHECKING:
    from collections.abc import Callable
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
        if value in {"WorkspaceWriteAny", "FileWrite"}:
            normalized_granted.update({"workspace_write_ephemeral", "workspace_write_tracked"})

    candidates = {_normalize_capability_token(requested)}
    mapped = lookup_ralph_capability(requested)
    if mapped is not None:
        candidates.add(_normalize_capability_token(mapped.value))
    if requested in {"WorkspaceWriteAny", "FileWrite"}:
        candidates.update({"workspace_write_ephemeral", "workspace_write_tracked"})
    return any(candidate in normalized_granted for candidate in candidates)


#: An exec-streaming sink paired with the thread ident that owns it. The pair
#: lives in ONE attribute so a concurrent reader performs a single (atomic)
#: attribute load and can never observe a torn owner/sink combination — the
#: TOCTOU that routed one request's exec output onto another's connection.
#: An owner of ``None`` means "any thread" — for single-tenant embeddings
#: (e.g. the FastMCP path, where dispatch hops to a worker thread); the
#: fallback HTTP server always stamps the real request-thread ident.
ToolOutputSinkEntry = tuple[int | None, "Callable[[dict[str, object]], None]"]


class McpSession(Protocol):
    """Full structural contract for MCP server session objects.

    Both implementations — the in-memory ``AgentSession`` (tests, FastMCP
    path) and the production ``FileBackedSession`` (standalone server via
    ``session_from_env``) — must satisfy this protocol. ``session_from_env``
    returns this type, so ``mypy ralph/`` (run by ``make verify``) enforces
    structural conformance of both; surface drift between the two shipped a
    production AttributeError that hung MCP clients (the -32001 retry storm).
    """

    # Settable variable member — the exec SSE path swaps it per request.
    tool_output_sink_entry: ToolOutputSinkEntry | None

    @property
    def session_id(self) -> str: ...
    @property
    def run_id(self) -> str: ...
    @property
    def drain(self) -> str: ...
    @property
    def capabilities(self) -> set[str]: ...
    @property
    def policy_flags(self) -> set[str] | None: ...
    @property
    def created_at(self) -> float: ...
    @property
    def parallel_worker(self) -> bool: ...
    @property
    def edit_area_result(self) -> object: ...
    @property
    def worker_artifact_dir(self) -> Path | None: ...
    @property
    def worker_namespace(self) -> Path | None: ...
    @property
    def allowed_roots(self) -> tuple[Path, ...]: ...
    @property
    def media_manifest(self) -> MediaManifest: ...
    @property
    def model_identity(self) -> MultimodalModelIdentity: ...
    @property
    def stored_capability_profile(self) -> ResolvedCapabilityProfile | None: ...
    @property
    def capability_profile(self) -> ResolvedCapabilityProfile | None: ...

    def check_capability(self, capability: str, /) -> object: ...

    def is_parallel_worker(self) -> bool: ...

    def check_edit_area(self, path: str, /) -> object: ...

    def current_thread_tool_output_sink(
        self,
    ) -> Callable[[dict[str, object]], None] | None: ...


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
    allowed_roots: tuple[Path, ...] = field(default_factory=tuple)
    media_manifest: MediaManifest = field(default_factory=MediaManifest)
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)
    stored_capability_profile: ResolvedCapabilityProfile | None = field(default=None)
    #: Atomic (owner thread ident, sink) pair for exec output streaming. The
    #: session is shared across concurrent request threads; without ownership,
    #: overlapping exec streams route output to whichever connection swapped
    #: the sink last. Stored as ONE attribute so readers can never tear it.
    tool_output_sink_entry: ToolOutputSinkEntry | None = field(default=None, repr=False)

    @property
    def capability_profile(self) -> ResolvedCapabilityProfile:
        """Return the stored profile when present, otherwise resolve from model_identity."""
        if self.stored_capability_profile is not None:
            return self.stored_capability_profile
        return resolve_capability_profile(self.model_identity)

    def check_capability(self, capability: str) -> object:
        return "approved" if session_has_capability(self.capabilities, capability) else "denied"

    def is_parallel_worker(self) -> bool:
        return self.parallel_worker

    def check_edit_area(self, _: str) -> object:
        return self.edit_area_result if self.edit_area_result is not None else "approved"

    def current_thread_tool_output_sink(self) -> Callable[[dict[str, object]], None] | None:
        """Return the sink only when the calling thread owns it.

        Dispatches capture this once at composition time; chunks from a
        request's subprocess reader threads then flow through the captured
        sink, immune to a concurrent request re-swapping the shared attribute.
        The (owner, sink) pair is read with a single attribute load, so a
        concurrent swap can never produce a torn owner/sink combination.
        """
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
