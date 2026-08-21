"""Media content block building, replay, and workspace media delivery."""

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
    """Build a non-fatal warning block naming the degraded multimodal delivery.

    S-7 (criterion 3) / S-6 (criterion 17): when a multimodal model
    identity is unknown or a modality verdict is ``UNSUPPORTED``, we
    DEGRADE GRACEFULLY rather than fail. The warning block is
    prepended to a usable result so the agent sees both the data it
    asked for (the inline image, resource-reference block, etc.) AND
    the operator-visible explanation of why this delivery mode is
    suboptimal.

    The exact block is plain text and is added BEFORE any other
    content block, so the multimodal payload still arrives intact.
    The accompanying ``logger.warning`` uses
    ``ralph.mcp.multimodal.degradation`` so tests can suppress it via
    ``caplog``. The text always names the ``provider``, ``model_id``,
    ``modality``, ``delivery_mode``, and ``verdict_reason`` so a future
    regression that drops any of these from the operator-visible
    warning fails this seam's pinning test.
    """
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
    """Explain an image the agent asked for but cannot be sent.

    Emitted when the delivery guard withholds inline image bytes because
    the agent CLI cannot round-trip them (see
    ``inline_image_roundtrip_unsafe``). The text is deliberately explicit
    that this is a dead end for this transport: sending the agent to
    re-read the handle, or to a retrieval route its CLI does not expose,
    wastes a turn and teaches it nothing.
    """
    message = (
        f"image '{title}' was NOT delivered as pixels. The '{transport}' agent CLI "
        "cannot carry an inline image block back into its model request, so sending "
        "one would fail the whole turn. Re-reading the handle "
        f"({uri}) returns this same reference, not the image -- do not retry it. "
        "Use read_media with format='metadata' for the file's size, sha256, and "
        "pixel dimensions, or work from the file path directly."
    )
    # INFO, not WARNING: for a restricted transport this is the designed
    # delivery path, not a degradation. Logging every withheld image at
    # WARNING made a correctly-working run look alarming.
    logger.info(message)
    return ToolContent.text_content(f"WARNING: {message}")


def _reference_delivery(delivery: DeliveryMode) -> DeliveryMode:
    """Return the delivery mode to stamp on a resource-reference block.

    A block being emitted AS a reference must say so. When an
    ``INLINE_IMAGE`` verdict is refused -- a non-image, or an image whose
    mime type is not inline-capable -- the reference used to carry the
    verdict's own ``inline_image`` value, which contradicts
    ``ResourceReferenceContent``'s contract and, more concretely, makes
    the artifact invisible to the handoff extractors that filter on the
    two reference values.
    """
    if delivery is DeliveryMode.RESOURCE_REFERENCE_REPLAY:
        return delivery
    # Including TYPED_BLOCK here reproduced the very bug this helper
    # exists to stop: a verdict naming a block type nothing can build
    # falls through to a reference, and stamping ``typed_block`` on it
    # left both handoff extractors -- which filter on the reference
    # values -- unable to see the artifact at all.
    return DeliveryMode.RESOURCE_REFERENCE_REPLAY


def _inline_image_deliverable(
    verdict: CapabilityVerdict,
    modality: str,
    mime_type: str,
) -> bool:
    """Return True when this artifact may be emitted as an inline image.

    An ``INLINE_IMAGE`` verdict alone is not enough. The verdict can come
    from a persisted profile, which is untrusted input -- and a stored
    ``pdf -> inline_image`` sent a PDF's bytes inside an ``ImageContent``
    block, a malformed request that kills the turn exactly like the
    incident this guard exists for. The artifact must actually BE an
    inline-capable image, the same pair of checks the fresh-read path
    makes.
    """
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
        delivery=_reference_delivery(verdict.delivery),
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
        delivery=_reference_delivery(verdict.delivery),
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
    """Build a degraded-delivery ResourceReferenceContent for UNSUPPORTED verdicts.

    S-6 (criterion 17): when the capability verdict is ``UNSUPPORTED``
    we still emit a usable resource-reference block. The URI is a
    deterministic, namespaced degraded handle so the agent can
    recognise the artefact type and that its delivery is degraded
    even though the cached bytes are not persisted for this path
    (the workspace read itself was rejected upstream of the
    resource-reference cache write).
    """
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
    # S-6 (criterion 17) / S-7 (criterion 3): an UNSUPPORTED verdict
    # still degrades gracefully -- the agent receives a WARNING block
    # naming provider / model_id / delivery_mode AND a usable
    # ResourceReferenceContent fallback. The default assumption is
    # multimodal-present; the warning is the operator-visible signal
    # that this delivery is the graceful-degradation path rather than
    # the optimal inline-or-typed-block path. ``is_error`` is False
    # so the call still surfaces multimodal-shaped content the agent
    # can act on.
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
    # S-6 (criterion 17): image payloads that fit the inline cap and
    # whose mime type is in the inline-image set are emitted as
    # ``ImageContent``. The capability verdict is not a gate for
    # image-only inline delivery -- criterion 14 ("unresolvable ->
    # capable") makes the inline path the default for any image the
    # runtime can read, regardless of how the provider / model identity
    # resolved. The unknown-identity -> warning-block prepending only
    # applies to the resource-reference path below.
    #
    # The ONE thing that does suppress the inline path is a transport
    # that provably cannot round-trip the block (see
    # ``_INLINE_IMAGE_ROUNDTRIP_UNSAFE_TRANSPORTS``). That is a measured
    # wire failure rather than a capability guess -- emitting the block
    # anyway kills the agent's next API request outright -- so it is not
    # covered by criterion 14. The check is on the identity, NOT on
    # ``verdict.delivery``: a caller-injected resource-reference verdict
    # on an unknown identity must still take the inline path.
    if (
        modality == "image"
        and mime_type in INLINE_IMAGE_MIME_TYPES
        and file_size <= max_inline_bytes
        and not inline_image_roundtrip_unsafe(profile.identity)
    ):
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        if not inline_image_requires_text_handle(profile.identity):
            return ToolResult(content=[ImageContent(data=encoded, mime_type=mime_type)], is_error=False)

        # Unknown clients such as CCS accept the standard MCP image union but
        # still need a Ralph-minted handle for the mandatory replay hop. Keep
        # the visible payload schema-valid and expose the handle as text.
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
    # Compute the artifact_id up-front so we can persist the cache and
    # pass a byte_loader to ``manifest.add`` at add-time. This lets
    # ``MediaManifest`` skip retaining the raw_bytes payload (the
    # loader provides bytes on demand). The pre-assigned artifact_id
    # is forwarded via ``MediaEntryExtras.artifact_id`` so the
    # manifest uses the SAME id we used to write the cache file.
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
    # S-7 (criterion 3): when the resolved identity is unknown, prepend a
    # WARNING block BEFORE the resource reference. The default assumption
    # is multimodal-present; the warning is the operator-visible signal
    # that this delivery is the graceful-degradation path rather than the
    # optimal inline-or-typed-block path. The identity-unknown -> warn
    # prepending is only used for the resource_reference path now; the
    # INLINE_IMAGE-eligible branch above returns early so this code never
    # runs for an inline-capable image.
    #
    # An image withheld for round-trip safety reaches here with a KNOWN
    # identity, and it needs the warning just as much: without it the
    # agent receives a bare resource reference for a file it can plainly
    # see is a PNG, with nothing saying why the bytes were withheld.
    # Scoped to ``image`` -- the guard says nothing about a PDF or an
    # audio file, whose delivery on this transport is unaffected.
    identity_unknown = not profile.identity.is_known()
    image_withheld = modality == "image" and inline_image_roundtrip_unsafe(profile.identity)
    warning_content: list[ToolContent] = []
    if image_withheld:
        # A withheld image needs its OWN wording. The generic degraded
        # block ends "delivered via resource-reference replay so the
        # agent can still proceed", which is not true here: this
        # transport cannot accept the bytes by any route Ralph Workflow
        # offers it, so re-reading the handle returns this same
        # reference. Saying so plainly beats sending the agent round a
        # loop looking for pixels it will never get.
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
    # The byte_loader and cache_path were wired at add-time, but
    # ``set_replay_source`` also records ``source_path`` on the
    # entry so downstream readers can recover it without re-walking
    # the workspace. The loader is the same instance.
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
        # Keep any warning: a handle-only provider on a restricted
        # transport would otherwise get a bare handle with nothing saying
        # why the pixels were withheld.
        return ToolResult(
            content=[*warning_content, ToolContent.text_content(f"Replay handle: {entry.uri}")],
            is_error=False,
        )
    if warning_content:
        return ToolResult(content=[*warning_content, block], is_error=False)
    return ToolResult(content=[block], is_error=False)
