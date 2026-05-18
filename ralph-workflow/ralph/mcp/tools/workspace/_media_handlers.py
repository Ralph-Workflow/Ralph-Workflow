"""Public media handler functions: read_media, read_image, persist_upstream_media."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ralph.mcp.multimodal.resources import (
    MediaSource,
    build_media_identity,
    parse_media_uri,
)
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._media_blocks import (
    _handle_replay_uri,
    _handle_workspace_media,
)
from ralph.mcp.tools.workspace._media_io import (
    _persist_media_session_entry,
    _write_durable_media_cache,
)
from ralph.mcp.tools.workspace._media_session import (
    _get_media_manifest,
    _get_session_capability_profile,
    _workspace_artifact_loader,
)
from ralph.mcp.tools.workspace._utils import (
    _SUPPORTED_IMAGE_MIME_TYPES,
    MEDIA_READ_CAPABILITY,
    infer_image_mime_type,
    normalize_relative_path,
    required_string_param,
)

if TYPE_CHECKING:
    from ralph.workspace import Workspace


def handle_read_media(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    max_inline_bytes: int = 5_242_880,
) -> ToolResult:
    """Read a media file or replay a stored artifact handle.

    Accepts either:
    - a workspace file path (e.g., ``screenshots/shot.png``)
    - a ``ralph://media/{artifact_id}`` replay handle from a prior session

    When given a replay handle, rehydrates the artifact from the live session
    manifest and returns the same typed block that was originally emitted.
    Invalid or unrecognised handles return an explicit structured failure.

    For workspace paths, delivery mode is determined by the session's model
    identity via the capability matrix: INLINE_IMAGE, TYPED_BLOCK,
    RESOURCE_REFERENCE_REPLAY, or UNSUPPORTED.
    """
    require_capability(session, MEDIA_READ_CAPABILITY, "Media read")
    path = required_string_param(params, "path")
    if path.startswith("ralph://media/"):
        return _handle_replay_uri(session, workspace, path)
    return _handle_workspace_media(session, workspace, path, max_inline_bytes)


def handle_read_image(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    max_inline_bytes: int = 5_242_880,
) -> ToolResult:
    """Read an image file and return it as a capability-aware content block.

    Requires MediaRead capability. Validates that the file is a supported image
    format, then delegates to the shared workspace media handler for delivery
    decision (inline image, typed block, or explicit unsupported/error).

    This is a compatibility alias over ``_handle_workspace_media`` that restricts
    inputs to image formats only while preserving the same truthful delivery
    contract as ``read_media``.
    """
    require_capability(session, MEDIA_READ_CAPABILITY, "Image read")
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)

    mime_type = infer_image_mime_type(normalized or path)
    if mime_type is None:
        suffix = PurePosixPath(path).suffix.lower() or "(none)"
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Unsupported image format '{suffix}'. "
                    f"Supported: {', '.join(sorted(_SUPPORTED_IMAGE_MIME_TYPES.keys()))}"
                )
            ],
            is_error=True,
        )

    return _handle_workspace_media(session, workspace, path, max_inline_bytes)


def _extract_resource_reference_replay_blocks(
    result: object,
) -> list[dict[str, str]]:
    """Extract resource_reference_replay blocks from a normalized upstream result."""
    if not isinstance(result, dict):
        return []
    raw_content: object = result.get("content")
    if not isinstance(raw_content, list):
        return []
    blocks: list[dict[str, str]] = []
    for item in raw_content:
        if not isinstance(item, dict):
            continue
        block: dict[str, str] = {k: str(v) for k, v in item.items() if isinstance(v, str)}
        if (
            block.get("type") == "resource_reference"
            and block.get("delivery") == "resource_reference_replay"
        ):
            blocks.append(block)
    return blocks


def _extract_resource_reference_blocks(
    result: object,
) -> list[dict[str, str]]:
    """Extract URI-backed resource_reference blocks from a normalized upstream result.

    These blocks reference external URIs (not Ralph-owned artifacts) and cannot
    be replayed across sessions. They are synthesized as unsupported_runtime_seam
    entries at the cross-session handoff boundary.
    """
    if not isinstance(result, dict):
        return []
    raw_content: object = result.get("content")
    if not isinstance(raw_content, list):
        return []
    blocks: list[dict[str, str]] = []
    for item in raw_content:
        if not isinstance(item, dict):
            continue
        block: dict[str, str] = {k: str(v) for k, v in item.items() if isinstance(v, str)}
        if (
            block.get("type") == "resource_reference"
            and block.get("delivery") == "resource_reference"
        ):
            blocks.append(block)
    return blocks


def persist_upstream_media_artifacts(
    result: object,
    session: object,
    workspace: Workspace,
) -> None:
    """Persist upstream embedded media artifacts to the durable cache and session index.

    Called after normalize_upstream_content_blocks so that:

    - resource_reference_replay blocks (backed by ralph://media/... URIs stored in
      the session manifest) are written to the durable cache and session index,
      enabling cross-session replay of artifacts from upstream embedded-data blocks.

    - URI-backed resource_reference blocks (delivery='resource_reference') reference
      external URIs and cannot be replayed across sessions. These are synthesized
      as unsupported_runtime_seam entries so the failure is explicit at invoke time.
    """
    replay_blocks = _extract_resource_reference_replay_blocks(result)
    uri_blocks = _extract_resource_reference_blocks(result)

    if not replay_blocks and not uri_blocks:
        return

    manifest = _get_media_manifest(session)
    profile = _get_session_capability_profile(session)

    if replay_blocks and manifest is not None:
        for block in replay_blocks:
            uri = block.get("uri", "")
            artifact_id = parse_media_uri(uri)
            if artifact_id is None:
                continue
            entry = manifest.get(artifact_id)
            if entry is None:
                continue
            verdict = profile.verdict_for(entry.modality)
            raw_bytes = entry.load_bytes()
            if raw_bytes is None:
                continue
            cache_path = _write_durable_media_cache(workspace, artifact_id, raw_bytes)
            identity_key = entry.identity_key or build_media_identity(
                modality=entry.modality,
                mime_type=entry.mime_type,
                title=entry.title,
                source=MediaSource(raw_bytes=raw_bytes),
            )
            entry.set_replay_source(
                cache_path=cache_path,
                byte_loader=_workspace_artifact_loader(workspace, cache_path, ""),
            )
            _persist_media_session_entry(
                session,
                workspace,
                {
                    "uri": uri,
                    "mime_type": entry.mime_type,
                    "title": entry.title,
                    "modality": entry.modality,
                    "delivery": "resource_reference_replay",
                    "reason": verdict.reason,
                    "source_path": "",
                    "cache_path": cache_path,
                    "source_uri": "",
                    "block_type": verdict.block_type or "",
                    "identity_key": identity_key,
                },
            )

    if uri_blocks:
        for block in uri_blocks:
            uri = block.get("uri", "")
            modality = block.get("modality", "unknown")
            title = block.get("title", uri.rsplit("/", maxsplit=1)[-1] or "untitled")
            mime_type = block.get("mimeType", "application/octet-stream")
            source_uri = uri
            reason = (
                f"Active runtime seam cannot carry {modality} content through the handoff path. "
                f"External URI-backed artifacts are not replayable across sessions."
            )
            _persist_media_session_entry(
                session,
                workspace,
                {
                    "uri": uri,
                    "mime_type": mime_type,
                    "title": title,
                    "modality": modality,
                    "delivery": "unsupported",
                    "reason": reason,
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": source_uri,
                    "block_type": "",
                    "failure_kind": "unsupported_runtime_seam",
                    "identity_key": build_media_identity(
                        modality=modality,
                        mime_type=mime_type,
                        title=title,
                        source=MediaSource(source_uri=source_uri),
                    ),
                },
            )
