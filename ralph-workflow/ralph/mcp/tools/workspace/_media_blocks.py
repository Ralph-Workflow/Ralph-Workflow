"""Media content block building, replay, and workspace media delivery."""

from __future__ import annotations

import base64
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from ralph.mcp.multimodal.artifacts import (
    INLINE_IMAGE_MIME_TYPES,
    AudioContent,
    DocumentContent,
    PdfContent,
    ResourceReferenceContent,
    VideoContent,
    infer_modality_and_mime,
)
from ralph.mcp.multimodal.capabilities import DeliveryMode
from ralph.mcp.multimodal.errors import MultimodalFailureKind
from ralph.mcp.multimodal.resources import (
    MediaEntryExtras,
    MediaSource,
    build_media_identity,
    parse_media_uri,
)
from ralph.mcp.tools.coordination import ImageContent, ToolContent, ToolResult
from ralph.mcp.tools.workspace._media_io import (
    _load_artifact_bytes,
    _load_persisted_registry_entry,
    _persist_media_session_entry,
    _write_durable_media_cache,
)
from ralph.mcp.tools.workspace._media_session import (
    _get_media_manifest,
    _get_session_capability_profile,
    _workspace_artifact_loader,
)
from ralph.mcp.tools.workspace._utils import (
    normalize_relative_path,
)

if TYPE_CHECKING:

    from ralph.mcp.multimodal.capabilities import CapabilityVerdict
    from ralph.mcp.multimodal.resources import ManifestEntry
    from ralph.mcp.tools.coordination import ContentBlock, CoordinationSessionLike
    from ralph.workspace import Workspace


def _make_typed_block(
    block_type: str,
    *,
    uri: str,
    mime_type: str,
    title: str,
) -> PdfContent | DocumentContent | AudioContent | VideoContent | None:
    """Build the correct typed content block for a TYPED_BLOCK verdict."""
    if block_type == "pdf":
        return PdfContent(uri=uri, mime_type=mime_type, title=title)
    if block_type == "document":
        return DocumentContent(uri=uri, mime_type=mime_type, title=title)
    if block_type == "audio":
        return AudioContent(uri=uri, mime_type=mime_type, title=title)
    if block_type == "video":
        return VideoContent(uri=uri, mime_type=mime_type, title=title)
    return None


def _make_non_inline_workspace_block(
    verdict: CapabilityVerdict,
    entry: ManifestEntry,
    mime_type: str,
    modality: str,
    title: str,
) -> tuple[ContentBlock, DeliveryMode]:
    """Return (content_block, delivery_mode) for non-inline workspace delivery."""
    if verdict.delivery == DeliveryMode.TYPED_BLOCK and verdict.block_type:
        block = _make_typed_block(
            verdict.block_type,
            uri=entry.uri,
            mime_type=mime_type,
            title=title,
        )
        if block is not None:
            return block, DeliveryMode.TYPED_BLOCK
    ref = ResourceReferenceContent(
        uri=entry.uri,
        mime_type=mime_type,
        title=title,
        modality=modality,
        delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
    )
    return ref, DeliveryMode.RESOURCE_REFERENCE_REPLAY


def _replay_from_manifest_entry(
    session: CoordinationSessionLike,
    entry: ManifestEntry,
) -> ToolResult:
    """Return the appropriate typed block from a live manifest entry."""
    profile = _get_session_capability_profile(session)
    verdict = profile.verdict_for(entry.modality)
    raw_bytes = entry.load_bytes()
    if verdict.delivery == DeliveryMode.INLINE_IMAGE:
        if raw_bytes is None:
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        f"{MultimodalFailureKind.MISSING_REPLAY_SOURCE}: "
                        f"Artifact '{entry.uri}' is no longer available from its replay source."
                    )
                ],
                is_error=True,
            )
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ToolResult(
            content=[ImageContent(data=encoded, mime_type=entry.mime_type)],
            is_error=False,
        )
    if verdict.delivery == DeliveryMode.TYPED_BLOCK and verdict.block_type:
        block = _make_typed_block(
            verdict.block_type,
            uri=entry.uri,
            mime_type=entry.mime_type,
            title=entry.title,
        )
        if block is not None:
            return ToolResult(content=[block], is_error=False)
    if verdict.delivery == DeliveryMode.UNSUPPORTED:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Modality '{entry.modality}' is not supported by provider "
                    f"'{verdict.provider}' (model: {verdict.model_id or 'unknown'}). "
                    f"Reason: {verdict.reason}"
                )
            ],
            is_error=True,
        )
    ref = ResourceReferenceContent(
        uri=entry.uri,
        mime_type=entry.mime_type,
        title=entry.title,
        modality=entry.modality,
        delivery=verdict.delivery,
    )
    return ToolResult(content=[ref], is_error=False)


def _replay_from_persisted_entry(
    session: CoordinationSessionLike,
    workspace: Workspace,
    persisted: dict[str, str],
    original_path: str,
) -> ToolResult:
    """Replay a media artifact from persisted v2 registry metadata."""
    cache_path = persisted.get("cache_path", "")
    source_path = persisted.get("source_path", "")
    modality = persisted.get("modality", "")
    mime_type = persisted.get("mime_type", "")
    title = persisted.get("title", "")
    block_type = persisted.get("block_type", "")
    uri = persisted.get("uri", original_path)

    raw_bytes = _load_artifact_bytes(workspace, cache_path, source_path)
    if raw_bytes is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"{MultimodalFailureKind.MISSING_REPLAY_SOURCE}: "
                    f"Artifact '{original_path}' was found in the registry but its "
                    f"cached bytes are no longer available "
                    f"(cache_path={cache_path!r}, source_path={source_path!r}). "
                    f"The original source may have been modified or removed."
                )
            ],
            is_error=True,
        )

    profile = _get_session_capability_profile(session)
    verdict = profile.verdict_for(modality)
    if verdict.delivery == DeliveryMode.INLINE_IMAGE:
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ToolResult(
            content=[ImageContent(data=encoded, mime_type=mime_type)],
            is_error=False,
        )
    if verdict.delivery == DeliveryMode.TYPED_BLOCK and block_type:
        block = _make_typed_block(block_type, uri=uri, mime_type=mime_type, title=title)
        if block is not None:
            return ToolResult(content=[block], is_error=False)
    if verdict.delivery == DeliveryMode.UNSUPPORTED:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Modality '{modality}' is not supported by provider "
                    f"'{verdict.provider}' (model: {verdict.model_id or 'unknown'}). "
                    f"Reason: {verdict.reason}"
                )
            ],
            is_error=True,
        )
    ref = ResourceReferenceContent(
        uri=uri,
        mime_type=mime_type,
        title=title,
        modality=modality,
        delivery=verdict.delivery,
    )
    return ToolResult(content=[ref], is_error=False)


def _handle_replay_uri(
    session: CoordinationSessionLike,
    workspace: Workspace,
    path: str,
) -> ToolResult:
    artifact_id = parse_media_uri(path)
    if artifact_id is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"{MultimodalFailureKind.INVALID_REPLAY_HANDLE}: "
                    f"'{path}' is not a valid ralph://media/{{artifact_id}} handle. "
                    f"Use the URI exactly as returned by a prior read_media call."
                )
            ],
            is_error=True,
        )
    manifest = _get_media_manifest(session)
    entry = manifest.get(artifact_id) if manifest is not None else None
    if entry is not None:
        return _replay_from_manifest_entry(session, entry)
    persisted = _load_persisted_registry_entry(workspace, artifact_id)
    if persisted is not None:
        return _replay_from_persisted_entry(session, workspace, persisted, path)
    return ToolResult(
        content=[
            ToolContent.text_content(
                f"{MultimodalFailureKind.MISSING_REPLAY_SOURCE}: "
                f"Artifact '{path}' is not available in the current session manifest "
                f"or the persisted registry. The artifact may be from an earlier session "
                f"whose cache has been cleared, or it was never created."
            )
        ],
        is_error=True,
    )


def _handle_workspace_media(
    session: CoordinationSessionLike,
    workspace: Workspace,
    path: str,
    max_inline_bytes: int,
) -> ToolResult:
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
    if verdict.delivery == DeliveryMode.UNSUPPORTED:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Modality '{modality}' is not supported by provider '{verdict.provider}' "
                    f"(model: {verdict.model_id or 'unknown'}). "
                    f"Accepted forms: typed_block or none. Reason: {verdict.reason}"
                )
            ],
            is_error=True,
        )
    abs_path = workspace.absolute_path(normalized or path)
    try:
        raw_bytes = Path(abs_path).read_bytes()
    except OSError as exc:
        return ToolResult(
            content=[ToolContent.text_content(f"Failed to read media file '{path}': {exc}")],
            is_error=True,
        )
    file_size = len(raw_bytes)
    title = PurePosixPath(path).name
    if (
        verdict.delivery == DeliveryMode.INLINE_IMAGE
        and modality == "image"
        and mime_type in INLINE_IMAGE_MIME_TYPES
        and file_size <= max_inline_bytes
    ):
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ToolResult(content=[ImageContent(data=encoded, mime_type=mime_type)], is_error=False)
    manifest = _get_media_manifest(session)
    if manifest is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Media file '{path}' ({modality}, {mime_type}) cannot be delivered: "
                    f"no active session manifest is available. "
                    f"Resource-reference delivery requires an active session."
                )
            ],
            is_error=True,
        )
    source_path = normalized or path
    identity_key = build_media_identity(
        modality=modality,
        mime_type=mime_type,
        title=title,
        source=MediaSource(source_path=source_path, raw_bytes=raw_bytes),
    )
    entry = manifest.add(
        title=title,
        mime_type=mime_type,
        modality=modality,
        raw_bytes=raw_bytes,
        extras=MediaEntryExtras(source_path=source_path, identity_key=identity_key),
    )
    block, delivery = _make_non_inline_workspace_block(verdict, entry, mime_type, modality, title)
    artifact_id = entry.uri.rsplit("/", maxsplit=1)[-1]
    cache_path = _write_durable_media_cache(workspace, artifact_id, raw_bytes)
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
            "delivery": delivery,
            "reason": verdict.reason,
            "source_path": source_path,
            "cache_path": cache_path,
            "source_uri": "",
            "block_type": verdict.block_type or "",
            "identity_key": identity_key,
        },
    )
    return ToolResult(content=[block], is_error=False)
