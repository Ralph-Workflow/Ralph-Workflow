from __future__ import annotations

import base64
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.multimodal.artifacts import (
    INLINE_IMAGE_MIME_TYPES,
    AudioContent,
    DocumentContent,
    ImageContent,
    PdfContent,
    ResourceReferenceContent,
    VideoContent,
    infer_modality_and_mime,
)
from ralph.mcp.multimodal.capabilities import (
    DeliveryMode,
    inline_image_requires_text_handle,
    inline_image_roundtrip_unsafe,
)
from ralph.mcp.multimodal.errors import MultimodalFailureKind
from ralph.mcp.multimodal.resources import (
    MediaEntryExtras,
    MediaSource,
    build_media_identity,
    new_artifact_id,
    parse_media_uri,
)
from ralph.mcp.tools.coordination import ToolContent, ToolResult
from ralph.mcp.tools.workspace._media_io import (
    _load_artifact_bytes,
    _load_persisted_registry_entry,
    _persist_media_session_entry,
    write_durable_media_cache,
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


def _build_warning_block(
    *,
    provider: str,
    modality: str,
    verdict_reason: str,
    model_id: str | None = None,
    delivery_mode: str = "unknown",
) -> ToolContent:
    resolved_model_id = model_id if model_id is not None else "unknown"
    message = (
        "multimodal degraded: provider="
        f"{provider!r} model_id={resolved_model_id!r} "
        f"modality={modality!r} delivery_mode={delivery_mode!r} "
        f"reason={verdict_reason!r}. "
        "Treating multimodal as ASSUMED-present per product criterion 3; "
        "the artifact below is delivered via resource-reference replay so the "
        "agent can still proceed."
    )
    logger.warning(message)
    return ToolContent.text_content(f"WARNING: {message}")


def _build_image_withheld_block(
    *,
    transport: str | None,
    title: str,
    uri: str,
) -> ToolContent:
    message = (
        f"image '{title}' was NOT delivered as pixels. The '{transport}' agent CLI "
        "cannot carry an inline image block back into its model request, so sending "
        "one would fail the whole turn. Re-reading the handle "
        f"({uri}) returns this same reference, not the image -- do not retry it. "
        "Use read_media with format='metadata' for the file's size, sha256, and "
        "pixel dimensions, or work from the file path directly."
    )
    logger.info(message)
    return ToolContent.text_content(f"WARNING: {message}")


def _inline_image_deliverable(
    verdict: CapabilityVerdict,
    modality: str,
    mime_type: str,
) -> bool:
    return (
        verdict.delivery == DeliveryMode.INLINE_IMAGE
        and modality == "image"
        and mime_type in INLINE_IMAGE_MIME_TYPES
    )


def _make_typed_block(
    block_type: str,
    *,
    uri: str,
    mime_type: str,
    title: str,
) -> PdfContent | DocumentContent | AudioContent | VideoContent | None:
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
    profile = _get_session_capability_profile(session)
    verdict = profile.verdict_for(entry.modality)
    raw_bytes = entry.load_bytes()
    if _inline_image_deliverable(verdict, entry.modality, entry.mime_type):
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
        if inline_image_requires_text_handle(profile.identity):
            return ToolResult(
                content=[ToolContent.text_content(f"Replay handle: {entry.uri}")],
                is_error=False,
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
        delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
    )
    return ToolResult(content=[ref], is_error=False)


def _replay_from_persisted_entry(
    session: CoordinationSessionLike,
    workspace: Workspace,
    persisted: dict[str, str],
    original_path: str,
) -> ToolResult:
    cache_path = persisted.get("cache_path", "")
    source_path = persisted.get("source_path", "")
    modality = persisted.get("modality", "")
    mime_type = persisted.get("mime_type", "")
    title = persisted.get("title", "")
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
    if _inline_image_deliverable(verdict, modality, mime_type):
        if inline_image_requires_text_handle(profile.identity):
            return ToolResult(
                content=[ToolContent.text_content(f"Replay handle: {uri}")],
                is_error=False,
            )
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ToolResult(
            content=[ImageContent(data=encoded, mime_type=mime_type)],
            is_error=False,
        )
    if verdict.delivery == DeliveryMode.TYPED_BLOCK and verdict.block_type:
        block = _make_typed_block(verdict.block_type, uri=uri, mime_type=mime_type, title=title)
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
        delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
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


def _build_unsupported_fallback_ref(
    *,
    modality: str,
    mime_type: str,
    title: str,
    source_key: str,
) -> ResourceReferenceContent:
    uri = f"ralph://media/degraded/{modality}/{source_key}"
    return ResourceReferenceContent(
        uri=uri,
        mime_type=mime_type,
        title=title,
        modality=modality,
        delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
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
    if verdict.delivery == DeliveryMode.UNSUPPORTED and modality != "image":
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Media file '{path}' ({modality}, {mime_type}) is not supported "
                    f"for provider '{verdict.provider}': {verdict.reason}"
                )
            ],
            is_error=True,
        )
    if verdict.delivery == DeliveryMode.UNSUPPORTED:
        warning = _build_warning_block(
            provider=verdict.provider,
            model_id=verdict.model_id,
            modality=modality,
            delivery_mode=verdict.delivery.value,
            verdict_reason=verdict.reason,
        )
        fallback = _build_unsupported_fallback_ref(
            modality=modality,
            mime_type=mime_type,
            title=PurePosixPath(path).name,
            source_key=normalized or path,
        )
        return ToolResult(content=[warning, fallback], is_error=False)
    abs_path = workspace.absolute_path(normalized or path)
    try:
        raw_bytes = DEFAULT_FILE_BACKEND.read_bytes(Path(abs_path))
    except OSError as exc:
        return ToolResult(
            content=[ToolContent.text_content(f"Failed to read media file '{path}': {exc}")],
            is_error=True,
        )
    file_size = len(raw_bytes)
    title = PurePosixPath(path).name
    if (
        modality == "image"
        and mime_type in INLINE_IMAGE_MIME_TYPES
        and file_size <= max_inline_bytes
        and not inline_image_roundtrip_unsafe(profile.identity)
    ):
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        if not inline_image_requires_text_handle(profile.identity):
            return ToolResult(
                content=[ImageContent(data=encoded, mime_type=mime_type)], is_error=False
            )

        manifest = _get_media_manifest(session)
        if manifest is None:
            return ToolResult(
                content=[ToolContent.text_content("No active session manifest is available.")],
                is_error=True,
            )
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
                "delivery": DeliveryMode.INLINE_IMAGE,
                "reason": verdict.reason,
                "source_path": source_path,
                "cache_path": cache_path,
                "source_uri": "",
                "block_type": "",
                "identity_key": identity_key,
            },
        )
        return ToolResult(
            content=[ToolContent.text_content(f"Replay handle: {entry.uri}")],
            is_error=False,
        )
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
    identity_unknown = not profile.identity.is_known()
    image_withheld = modality == "image" and inline_image_roundtrip_unsafe(profile.identity)
    warning_content: list[ToolContent] = []
    if image_withheld:
        warning_content.append(
            _build_image_withheld_block(
                transport=profile.identity.transport,
                title=title,
                uri=entry.uri,
            )
        )
    elif identity_unknown:
        warning_content.append(
            _build_warning_block(
                provider=verdict.provider,
                model_id=verdict.model_id,
                modality=modality,
                delivery_mode=verdict.delivery.value,
                verdict_reason=verdict.reason,
            )
        )

    block, delivery = _make_non_inline_workspace_block(verdict, entry, mime_type, modality, title)
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
    if inline_image_requires_text_handle(profile.identity):
        return ToolResult(
            content=[*warning_content, ToolContent.text_content(f"Replay handle: {entry.uri}")],
            is_error=False,
        )
    if warning_content:
        return ToolResult(content=[*warning_content, block], is_error=False)
    return ToolResult(content=[block], is_error=False)
