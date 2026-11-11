
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
    from collections.abc import Callable

    from ralph.workspace import Workspace


def _get_media_manifest(session: object) -> MediaManifest | None:
    raw: object = getattr(session, "media_manifest", None)
    if isinstance(raw, MediaManifest):
        return raw
    return None


def _get_session_model_identity(session: object) -> MultimodalModelIdentity:
    raw: object = getattr(session, "model_identity", None)
    if isinstance(raw, MultimodalModelIdentity):
        return raw
    return UNKNOWN_IDENTITY


def _get_session_capability_profile(session: object) -> ResolvedCapabilityProfile:
    caller: object = getattr(session, "caller_capability_profile", None)
    if isinstance(caller, ResolvedCapabilityProfile):
        return caller
    raw: object = getattr(session, "capability_profile", None)
    if isinstance(raw, ResolvedCapabilityProfile):
        return raw
    return resolve_capability_profile(_get_session_model_identity(session))


def _workspace_artifact_loader(
    workspace: Workspace,
    cache_path: str,
    source_path: str,
) -> Callable[[], bytes | None]:

    def _loader() -> bytes | None:
        return _load_artifact_bytes(workspace, cache_path, source_path)

    return _loader
