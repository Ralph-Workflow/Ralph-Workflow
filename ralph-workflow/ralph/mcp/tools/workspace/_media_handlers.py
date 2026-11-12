from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.multimodal.artifacts import infer_modality_and_mime
from ralph.mcp.multimodal.resources import (
    MediaSource,
    build_media_identity,
    parse_media_uri,
)
from ralph.mcp.tools._envelope_bytes import finalize_envelope_bytes_out
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
    write_durable_media_cache,
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
    raw = params.get("format", "inline")
    if raw is None or raw == "inline":
        return "inline"
    if raw == "metadata":
        return "metadata"
    return "__invalid__"


def _png_dimensions(raw_bytes: bytes) -> tuple[int | None, int | None]:
    png_header_size = 24
    if len(raw_bytes) < png_header_size:
        return (None, None)
    if raw_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return (None, None)
    if raw_bytes[12:16] != b"IHDR":
        return (None, None)
    try:
        width = int.from_bytes(raw_bytes[16:20], byteorder="big", signed=False)
        height = int.from_bytes(raw_bytes[20:24], byteorder="big", signed=False)
    except ValueError:
        return (None, None)
    return (width, height)


def _build_media_metadata_envelope(
    *,
    path: str,
    raw_bytes: bytes,
    modality: str,
    mime_type: str,
    resource_handle: str | None,
    title: str,
) -> ToolResult:
    base_envelope: dict[str, object] = {
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
        base_envelope["width"] = width
        base_envelope["height"] = height
    envelope = finalize_envelope_bytes_out(base_envelope)
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(envelope, separators=(",", ":")))],
        is_error=False,
    )


def _raise_invalid_format(value: object) -> ToolResult:
    from ralph.mcp.tools.coordination import InvalidParamsError

    raise InvalidParamsError(f"Invalid format: {value!r}; expected 'inline' or 'metadata'")


def handle_read_media(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    max_inline_bytes: int = 5_242_880,
) -> ToolResult:
    require_capability(session, MEDIA_READ_CAPABILITY, "Media read")
    path = required_string_param(params, "path")
    format_value = _parse_format_param(params)
    if format_value == "__invalid__":
        _raise_invalid_format(params.get("format"))
    if path.startswith("ralph://media/"):
        if format_value == "metadata":
            return _build_replay_metadata_envelope(session, workspace, path)
        return _handle_replay_uri(session, workspace, path)
    if format_value == "metadata":
        return _build_workspace_media_metadata(session, workspace, path, max_inline_bytes)
    return _handle_workspace_media(session, workspace, path, max_inline_bytes)


def _build_replay_metadata_envelope(
    session: CoordinationSessionLike,
    workspace: Workspace,
    path: str,
) -> ToolResult:
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
    from ralph.mcp.multimodal.capabilities import (
        DeliveryMode,
        inline_image_requires_text_handle,
        inline_image_roundtrip_unsafe,
    )
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
        raw_bytes = DEFAULT_FILE_BACKEND.read_bytes(Path(abs_path))
    except OSError as exc:
        return ToolResult(
            content=[ToolContent.text_content(f"Failed to read media file '{path}': {exc}")],
            is_error=True,
        )
    title = PurePosixPath(path).name
    resource_handle: str | None = None
    if (
        verdict.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY
        and (modality != "image" or inline_image_roundtrip_unsafe(profile.identity))
    ) or inline_image_requires_text_handle(profile.identity):
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
            cache_path = write_durable_media_cache(workspace, artifact_id, raw_bytes)
            entry = manifest.add(
                title=title,
                mime_type=mime_type,
                modality=modality,
                raw_bytes=raw_bytes,
                extras=MediaEntryExtras(
                    source_path=source_path,
                    identity_key=identity_key,
                    cache_path=cache_path,
                    byte_loader=_workspace_artifact_loader(workspace, cache_path, source_path),
                    artifact_id=artifact_id,
                ),
            )
            entry.set_replay_source(
                cache_path=cache_path,
                source_path=source_path,
                byte_loader=_workspace_artifact_loader(workspace, cache_path, source_path),
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
        return _build_workspace_media_metadata(session, workspace, path, max_inline_bytes)

    return _handle_workspace_media(session, workspace, path, max_inline_bytes)


def _extract_resource_reference_replay_blocks(
    result: object,
) -> list[dict[str, str]]:
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
            cache_path = write_durable_media_cache(workspace, artifact_id, raw_bytes)
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
