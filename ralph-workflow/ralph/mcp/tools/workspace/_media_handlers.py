"""Public media handler functions: read_media, read_image, persist_upstream_media."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ralph.mcp.multimodal.artifacts import infer_modality_and_mime
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


def _parse_format_param(params: dict[str, object]) -> str:
    """Parse the optional ``format`` parameter.

    ``read_image`` / ``read_media`` accept ``format='inline'`` (default,
    preserves the legacy block) or ``format='metadata'`` (bounded
    metadata envelope with no inline bytes). An unknown value returns
    ``'__invalid__'`` so callers can raise ``InvalidParamsError`` with
    a structured message naming the closed enum.
    """
    raw = params.get("format", "inline")
    if raw is None or raw == "inline":
        return "inline"
    if raw == "metadata":
        return "metadata"
    return "__invalid__"


def _png_dimensions(raw_bytes: bytes) -> tuple[int | None, int | None]:
    """Return the (width, height) of a PNG payload, or (None, None).

    The parser inspects only the IHDR chunk so it does not require
    PIL/Pillow; only the ``width`` (4 bytes, big-endian, offset 16)
    and ``height`` (4 bytes, big-endian, offset 20) are read. Returns
    (None, None) for any payload that is not a recognizable PNG
    signature so callers never raise on a malformed header.
    """
    # PNG signature (8) + IHDR length (4) + IHDR type (4) + width (4) + height (4).
    png_header_size = 24
    if len(raw_bytes) < png_header_size:
        return (None, None)
    if raw_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return (None, None)
    if raw_bytes[12:16] != b"IHDR":
        return (None, None)
    try:
        # ``int.from_bytes`` is the stdlib alternative to ``struct.unpack``
        # and avoids the ``tuple[Any, ...]`` Any-leak from the
        # ``struct`` typeshed. ``byteorder="big"`` matches the PNG
        # big-endian format.
        width = int.from_bytes(raw_bytes[16:20], byteorder="big", signed=False)
        height = int.from_bytes(raw_bytes[20:24], byteorder="big", signed=False)
    except ValueError:
        return (None, None)
    return (width, height)


def _build_image_metadata_envelope(
    *,
    path: str,
    mime_type: str,
    raw_bytes: bytes,
    max_inline_bytes: int,
) -> ToolResult:
    """Build the ``format='metadata'`` envelope for ``handle_read_image``.

    The envelope is bounded: it returns mime_type, size_bytes, sha256,
    width, height (PNG only), and an ``inline_only`` flag. The image
    bytes are dropped; ``handle_read_image`` never persists a
    Ralph-owned ``ralph://media/{artifact_id}`` artifact so the
    ``resource_handle`` is always ``None``.
    """
    width, height = _png_dimensions(raw_bytes)
    envelope: dict[str, object] = {
        "format": "metadata",
        "media_kind": "image",
        "mime_type": mime_type,
        "size_bytes": len(raw_bytes),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "width": width,
        "height": height,
        "resource_handle": None,
        "inline_only": True,
        "bytes_in": len(raw_bytes),
        "truncated": False,
        "max_inline_bytes": max_inline_bytes,
    }
    serialized = json.dumps(envelope).encode("utf-8")
    envelope["bytes_out"] = len(serialized)
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(envelope))],
        is_error=False,
    )


def _build_media_metadata_envelope(
    *,
    path: str,
    raw_bytes: bytes,
    modality: str,
    mime_type: str,
    resource_handle: str | None,
    title: str,
) -> ToolResult:
    """Build the ``format='metadata'`` envelope for ``handle_read_media``.

    For non-image media the existing ``_handle_workspace_media`` path
    has already registered a Ralph-owned ``ralph://media/{artifact_id}``
    artifact (because the original delivery was a resource reference),
    so the resource_handle is preserved. For inline images the
    artifact was never persisted, so the handle is ``None``.
    """
    envelope: dict[str, object] = {
        "format": "metadata",
        "media_kind": modality,
        "mime_type": mime_type,
        "size_bytes": len(raw_bytes),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "resource_handle": resource_handle,
        "inline_only": resource_handle is None,
        "bytes_in": len(raw_bytes),
        "truncated": False,
        "title": title,
        "path": path,
    }
    if modality == "image":
        width, height = _png_dimensions(raw_bytes)
        envelope["width"] = width
        envelope["height"] = height
    serialized = json.dumps(envelope).encode("utf-8")
    envelope["bytes_out"] = len(serialized)
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(envelope))],
        is_error=False,
    )


def _raise_invalid_format(value: object) -> ToolResult:
    """Return a structured ``InvalidParamsError`` for an unknown format."""
    from ralph.mcp.tools.coordination import InvalidParamsError

    raise InvalidParamsError(
        f"Invalid format: {value!r}; expected 'inline' or 'metadata'"
    )


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
    manifest and returns the same typed block that was originally emitted
    (``format='inline'``) or a bounded metadata envelope (``format='metadata'``).
    Invalid or unrecognised handles return an explicit structured failure.

    For workspace paths, delivery mode is determined by the session's model
    identity via the capability matrix: INLINE_IMAGE, TYPED_BLOCK,
    RESOURCE_REFERENCE_REPLAY, or UNSUPPORTED.

    The optional ``format`` argument (``'inline'|'metadata'``) selects
    between the legacy block shape and a bounded metadata envelope. The
    metadata envelope drops inline media bytes and exposes a replayable
    ``resource_handle`` when the underlying delivery registered one.
    """
    require_capability(session, MEDIA_READ_CAPABILITY, "Media read")
    path = required_string_param(params, "path")
    format_value = _parse_format_param(params)
    if format_value == "__invalid__":
        _raise_invalid_format(params.get("format"))
    if path.startswith("ralph://media/"):
        # Replay handles never go through ``format='metadata'`` because
        # they rehydrate from the persisted artifact bytes, which would
        # defeat the bounded-bytes contract. We still allow metadata
        # mode so callers can inspect a known artifact without
        # downloading its body again.
        if format_value == "metadata":
            return _build_replay_metadata_envelope(session, workspace, path)
        return _handle_replay_uri(session, workspace, path)
    if format_value == "metadata":
        return _build_workspace_media_metadata(
            session, workspace, path, max_inline_bytes
        )
    return _handle_workspace_media(session, workspace, path, max_inline_bytes)


def _build_replay_metadata_envelope(
    session: CoordinationSessionLike,
    workspace: Workspace,
    path: str,
) -> ToolResult:
    """Return a bounded metadata envelope for a replay handle."""
    from ralph.mcp.multimodal.errors import MultimodalFailureKind
    from ralph.mcp.tools.workspace._media_io import (
        _load_artifact_bytes,
        _load_persisted_registry_entry,
    )

    artifact_id = parse_media_uri(path)
    if artifact_id is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"{MultimodalFailureKind.INVALID_REPLAY_HANDLE}: "
                    f"'{path}' is not a valid ralph://media/{{artifact_id}} handle."
                )
            ],
            is_error=True,
        )
    manifest = _get_media_manifest(session)
    entry = manifest.get(artifact_id) if manifest is not None else None
    if entry is not None:
        return _build_media_metadata_envelope(
            path=path,
            raw_bytes=entry.load_bytes() or b"",
            modality=entry.modality,
            mime_type=entry.mime_type,
            resource_handle=entry.uri,
            title=entry.title,
        )
    persisted = _load_persisted_registry_entry(workspace, artifact_id)
    if persisted is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"{MultimodalFailureKind.MISSING_REPLAY_SOURCE}: "
                    f"Artifact '{path}' is not available."
                )
            ],
            is_error=True,
        )
    cache_path = persisted.get("cache_path", "")
    source_path = persisted.get("source_path", "")
    raw_bytes = _load_artifact_bytes(workspace, cache_path, source_path) or b""
    modality = persisted.get("modality", "file")
    mime_type = persisted.get("mime_type", "application/octet-stream")
    title = persisted.get("title", "")
    return _build_media_metadata_envelope(
        path=path,
        raw_bytes=raw_bytes,
        modality=modality,
        mime_type=mime_type,
        resource_handle=persisted.get("uri", path),
        title=title,
    )


def _build_workspace_media_metadata(
    session: CoordinationSessionLike,
    workspace: Workspace,
    path: str,
    max_inline_bytes: int,
) -> ToolResult:
    """Return a bounded metadata envelope for a workspace media file.

    Ponytail: the inline delivery path is bypassed entirely; only the
    minimum amount of work needed to emit the envelope is performed.
    For inline-image-eligible files we still need to read the file
    bytes to compute sha256 + size; for resource-reference deliveries
    we run the same workspace path but never emit the inline block.
    """
    from ralph.mcp.multimodal.capabilities import DeliveryMode
    from ralph.mcp.multimodal.resources import (
        MediaEntryExtras,
        new_artifact_id,
    )
    from ralph.mcp.tools.workspace._media_session import (
        _workspace_artifact_loader,
    )

    normalized = normalize_relative_path(path)
    suffix = PurePosixPath(normalized or path).suffix.lower()
    inferred = infer_modality_and_mime(suffix)
    if inferred is None:
        supported = sorted(
            {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".pdf",
                ".mp3",
                ".wav",
                ".ogg",
                ".m4a",
                ".flac",
                ".aac",
                ".mp4",
                ".avi",
                ".mov",
                ".mkv",
                ".webm",
                ".docx",
                ".pptx",
                ".xlsx",
            }
        )
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Unsupported media format '{suffix or '(none)'}'. "
                    f"Supported: {', '.join(supported)}"
                )
            ],
            is_error=True,
        )
    modality, mime_type = inferred
    profile = _get_session_capability_profile(session)
    verdict = profile.verdict_for(modality)
    abs_path = workspace.absolute_path(normalized or path)
    from pathlib import Path

    try:
        raw_bytes = Path(abs_path).read_bytes()
    except OSError as exc:
        return ToolResult(
            content=[ToolContent.text_content(f"Failed to read media file '{path}': {exc}")],
            is_error=True,
        )
    title = PurePosixPath(path).name
    # If the original delivery would have registered a Ralph-owned
    # artifact (resource_reference_replay path), register it here too
    # so the resource_handle in the metadata envelope is replayable.
    # Inline-image deliveries never persist an artifact.
    resource_handle: str | None = None
    if (
        verdict.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY
        and modality != "image"
    ):
        manifest = _get_media_manifest(session)
        if manifest is not None:
            source_path = normalized or path
            identity_key = build_media_identity(
                modality=modality,
                mime_type=mime_type,
                title=title,
                source=MediaSource(source_path=source_path, raw_bytes=raw_bytes),
            )
            artifact_id = new_artifact_id()
            cache_path = _write_durable_media_cache(workspace, artifact_id, raw_bytes)
            entry = manifest.add(
                title=title,
                mime_type=mime_type,
                modality=modality,
                raw_bytes=raw_bytes,
                extras=MediaEntryExtras(
                    source_path=source_path,
                    identity_key=identity_key,
                    cache_path=cache_path,
                    byte_loader=_workspace_artifact_loader(
                        workspace, cache_path, source_path
                    ),
                    artifact_id=artifact_id,
                ),
            )
            entry.set_replay_source(
                cache_path=cache_path,
                source_path=source_path,
                byte_loader=_workspace_artifact_loader(
                    workspace, cache_path, source_path
                ),
            )
            _persist_media_session_entry(
                session,
                workspace,
                {
                    "uri": entry.uri,
                    "mime_type": mime_type,
                    "title": title,
                    "modality": modality,
                    "delivery": DeliveryMode.RESOURCE_REFERENCE_REPLAY,
                    "reason": verdict.reason,
                    "source_path": source_path,
                    "cache_path": cache_path,
                    "source_uri": "",
                    "block_type": verdict.block_type or "",
                    "identity_key": identity_key,
                },
            )
            resource_handle = entry.uri
    return _build_media_metadata_envelope(
        path=path,
        raw_bytes=raw_bytes,
        modality=modality,
        mime_type=mime_type,
        resource_handle=resource_handle,
        title=title,
    )


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

    The optional ``format`` argument (``'inline'|'metadata'``) selects
    between the legacy image content block (``format='inline'``, default)
    and a bounded metadata envelope (``format='metadata'``). The metadata
    envelope drops the image bytes inline and exposes size, sha256,
    width, height, and an ``inline_only`` flag. ``handle_read_image``
    never persists a ``ralph://media/{artifact_id}`` artifact, so the
    envelope's ``resource_handle`` is always ``None``.

    This is a compatibility alias over ``_handle_workspace_media`` that restricts
    inputs to image formats only while preserving the same truthful delivery
    contract as ``read_media``.
    """
    require_capability(session, MEDIA_READ_CAPABILITY, "Image read")
    path = required_string_param(params, "path")
    format_value = _parse_format_param(params)
    if format_value == "__invalid__":
        _raise_invalid_format(params.get("format"))
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

    if format_value == "metadata":
        from pathlib import Path

        abs_path = workspace.absolute_path(normalized or path)
        try:
            raw_bytes = Path(abs_path).read_bytes()
        except OSError as exc:
            return ToolResult(
                content=[ToolContent.text_content(f"Failed to read media file '{path}': {exc}")],
                is_error=True,
            )
        return _build_image_metadata_envelope(
            path=path,
            mime_type=mime_type,
            raw_bytes=raw_bytes,
            max_inline_bytes=max_inline_bytes,
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
