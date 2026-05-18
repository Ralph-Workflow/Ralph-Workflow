"""Session-level media helpers: manifest access, capability profiles, identity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.tools.workspace._media_io import _load_artifact_bytes

if TYPE_CHECKING:
    from ralph.workspace import Workspace

_MEDIA_SESSION_SCHEMA_VERSION = "2"


def _get_media_manifest(session: object) -> MediaManifest | None:
    """Return the session's MediaManifest if available."""
    raw: object = getattr(session, "media_manifest", None)
    if isinstance(raw, MediaManifest):
        return raw
    return None


def _get_session_model_identity(session: object) -> MultimodalModelIdentity:
    """Extract the model identity from a session, defaulting to UNKNOWN_IDENTITY."""
    raw: object = getattr(session, "model_identity", None)
    if isinstance(raw, MultimodalModelIdentity):
        return raw
    return UNKNOWN_IDENTITY


def _get_session_capability_profile(session: object) -> ResolvedCapabilityProfile:
    """Return the resolved capability profile from a session.

    Prefers a pre-resolved profile from the session (populated by the managed
    runtime path from the persisted session contract), falling back to
    computing one from the session's model identity.
    """
    raw: object = getattr(session, "capability_profile", None)
    if isinstance(raw, ResolvedCapabilityProfile):
        return raw
    return resolve_capability_profile(_get_session_model_identity(session))


def _workspace_artifact_loader(
    workspace: Workspace,
    cache_path: str,
    source_path: str,
) -> object:
    """Build a lazy artifact loader bound to a workspace replay source."""
    def _loader() -> bytes | None:
        return _load_artifact_bytes(workspace, cache_path, source_path)

    return _loader


def _media_session_identity(entry: dict[str, str]) -> str:
    """Return the dedupe identity for a persisted media-session entry."""
    identity_key = entry.get("identity_key", "")
    if identity_key:
        return identity_key
    source_uri = entry.get("source_uri", "")
    source_path = entry.get("source_path", "")
    modality = entry.get("modality", "")
    artifact_id = entry.get("artifact_id", "")
    uri = entry.get("uri", "")
    if source_uri:
        return f"source-uri:{modality}:{source_uri}"
    if source_path:
        return f"source-path:{modality}:{source_path}"
    return f"artifact-id:{artifact_id or uri}"
