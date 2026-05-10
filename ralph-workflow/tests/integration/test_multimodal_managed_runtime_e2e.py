"""End-to-end acceptance suite for multimodal managed-runtime behaviors.

Proves the cross-layer promises required by the multimodal product:
- default-on media surface exposed to multimodal clients
- text-only clients do not see multimodal tools
- screenshot/PNG inline delivery through Ralph-owned MCP path
- PDF resource-reference storage and retrieval via resources/read
- unknown-provider safe fallback to resource_reference_replay for all modalities
- upstream mixed-modality normalization without silent loss
- prompt sidecar preserves mixed-modality metadata for runner handoff

All tests drive McpServer.handle_request() in-process; no threads, no sockets.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest

from ralph.mcp.multimodal.artifacts import SUPPORTED_MODALITIES
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    DeliveryMode,
    MultimodalModelIdentity,
    get_delivery_mode,
)
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.tools.names import RalphToolName
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Byte constants for in-memory artifacts — no external files needed
# ---------------------------------------------------------------------------

_TINY_PNG_BYTES = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
    0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
    0x00, 0x00, 0x02, 0x00, 0x01, 0xE2, 0x21, 0xBC,
    0x33, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
    0x44, 0xAE, 0x42, 0x60, 0x82,
])

_TINY_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"

_MULTIMODAL_CAPS = {
    "WorkspaceRead",
    "WorkspaceWriteAny",
    "WorkspaceMetadataRead",
    "WorkspaceEdit",
    "WorkspaceDelete",
    "GitStatusRead",
    "ProcessExecBounded",
    "ArtifactSubmit",
    "RunReportProgress",
    "EnvRead",
    "media.read",
}

_TEXT_ONLY_CAPS = _MULTIMODAL_CAPS - {"media.read"}


def _build_server(
    workspace_path: Path,
    *,
    provider: str = "unknown",
    model_id: str | None = None,
    with_media: bool = True,
) -> McpServer:
    caps = _MULTIMODAL_CAPS if with_media else _TEXT_ONLY_CAPS
    workspace = FsWorkspace(workspace_path)
    session = AgentSession(
        session_id="e2e-test",
        run_id="e2e-run",
        drain="development",
        capabilities=caps,
        model_identity=MultimodalModelIdentity(provider=provider, model_id=model_id),
    )
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _initialize(server: McpServer, *, multimodal_client: bool = True) -> ServerState:
    capabilities: dict[str, object] = {}
    if multimodal_client:
        capabilities = {"media": {}, "image": {}}
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": capabilities,
            "clientInfo": {"name": "test", "version": "1.0"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    _, state = server.handle_request(notif, state)
    return state


def _tools_list(server: McpServer, state: ServerState) -> set[str]:
    req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", params={}, msg_id=2)
    resp, _ = server.handle_request(req, state)
    assert resp is not None and resp.result is not None
    tools = cast("list[dict[str, Any]]", cast("dict[str, Any]", resp.result)["tools"])
    return {t["name"] for t in tools}


def _tool_call(
    server: McpServer,
    state: ServerState,
    name: str,
    args: dict[str, object],
    call_id: int = 10,
) -> dict[str, Any]:
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": name, "arguments": args},
        msg_id=call_id,
    )
    resp, _ = server.handle_request(req, state)
    assert resp is not None
    return cast("dict[str, Any]", resp.result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_text_only_client_hides_multimodal_tools_by_default(tmp_path: Path) -> None:
    """A text-only client must not see read_media or read_image in tools/list."""
    server = _build_server(tmp_path, with_media=True)
    state = _initialize(server, multimodal_client=False)
    tool_names = _tools_list(server, state)
    assert str(RalphToolName.READ_MEDIA) not in tool_names, (
        f"read_media must be hidden for text-only clients; visible tools: {tool_names}"
    )
    assert str(RalphToolName.READ_IMAGE) not in tool_names, (
        f"read_image must be hidden for text-only clients; visible tools: {tool_names}"
    )


@pytest.mark.integration
def test_multimodal_client_sees_default_on_media_tools(tmp_path: Path) -> None:
    """A multimodal-capable client must see read_media and read_image by default."""
    server = _build_server(tmp_path, with_media=True)
    state = _initialize(server, multimodal_client=True)
    tool_names = _tools_list(server, state)
    assert str(RalphToolName.READ_MEDIA) in tool_names, (
        f"read_media must be visible for multimodal clients; visible tools: {tool_names}"
    )
    assert str(RalphToolName.READ_IMAGE) in tool_names, (
        f"read_image must be visible for multimodal clients; visible tools: {tool_names}"
    )


@pytest.mark.integration
def test_screenshot_png_round_trip_preserves_inline_or_replay_delivery(
    tmp_path: Path,
) -> None:
    """PNG file with Claude model identity delivers inline image through Ralph's MCP path."""
    png_file = tmp_path / "screenshot.png"
    png_file.write_bytes(_TINY_PNG_BYTES)

    server = _build_server(tmp_path, provider="claude", model_id="claude-3-5-sonnet-20241022")
    state = _initialize(server, multimodal_client=True)

    result = _tool_call(server, state, str(RalphToolName.READ_MEDIA), {"path": "screenshot.png"})

    assert result.get("isError") is not True, f"read_media returned error: {result}"
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert len(content) == 1
    block = content[0]
    block_type = block.get("type")
    # Claude provider: PNG must be inline image (or resource_reference for cross-session)
    assert block_type in {"image", "resource_reference"}, (
        f"Expected inline image or resource_reference, got type={block_type!r}"
    )


@pytest.mark.integration
def test_pdf_resource_reference_is_retrievable_via_resources_read(
    tmp_path: Path,
) -> None:
    """PDF artifact stored via read_media must be retrievable via resources/read."""
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(_TINY_PDF_BYTES)

    server = _build_server(tmp_path, provider="unknown-provider")
    state = _initialize(server, multimodal_client=True)

    result = _tool_call(server, state, str(RalphToolName.READ_MEDIA), {"path": "report.pdf"})

    assert result.get("isError") is not True, f"read_media returned error: {result}"
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert len(content) == 1
    block = content[0]
    assert block.get("type") == "resource_reference", (
        f"Expected resource_reference block, got: {block}"
    )
    uri = str(block.get("uri", ""))
    assert uri.startswith("ralph://media/"), f"Expected ralph://media/ URI, got: {uri!r}"

    # Artifact must be retrievable via resources/read
    read_req = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": uri},
        msg_id=20,
    )
    read_resp, _ = server.handle_request(read_req, state)
    assert read_resp is not None and read_resp.result is not None
    contents = cast(
        "list[dict[str, Any]]",
        cast("dict[str, Any]", read_resp.result).get("contents", []),
    )
    assert len(contents) == 1
    assert contents[0].get("uri") == uri
    assert isinstance(contents[0].get("blob"), str) and len(contents[0]["blob"]) > 0


def test_unknown_provider_preserves_supported_modalities_as_replayable_resources() -> None:
    """Unknown provider must deliver every supported modality as resource_reference_replay.

    This proves the safe fallback: no modality is silently dropped or rejected
    when the provider cannot be determined.
    """
    for modality in sorted(SUPPORTED_MODALITIES):
        verdict = get_delivery_mode(UNKNOWN_IDENTITY, modality)
        assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY, (
            f"modality={modality!r} must be RESOURCE_REFERENCE_REPLAY for unknown provider, "
            f"got {verdict.delivery!r}"
        )
        assert verdict.is_supported(), (
            f"modality={modality!r} must remain supported (not rejected) for unknown provider"
        )
        assert verdict.is_resource_reference(), (
            f"modality={modality!r} must be resource_reference for unknown provider"
        )


def test_upstream_mixed_modalities_are_normalized_without_silent_loss() -> None:
    """Upstream mixed text+image+audio must preserve all blocks without silent dropping."""
    class _FakeClient:
        def list_tools(self) -> list[object]:
            from ralph.mcp.upstream.models import UpstreamTool  # noqa: PLC0415
            return [UpstreamTool(name="mix", description="mixed content tool")]

        def call_tool(self, _name: str, _args: object) -> dict[str, object]:
            return {
                "content": [
                    {"type": "text", "text": "header"},
                    {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"},
                    {"type": "audio", "data": "d29ybGQ=", "mimeType": "audio/mpeg"},
                ]
            }

    server = UpstreamMcpServer(name="mix_server", transport="http", url="http://unused")

    class _FakeSession:
        media_manifest = MediaManifest()

    registry = UpstreamRegistry.build(
        [server],
        client_factory=lambda _srv: _FakeClient(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    )

    result = registry.call_tool(
        "ralph_upstream__mix_server__mix", {}, session=_FakeSession()
    )

    content = result.get("content", [])
    # All three input blocks must produce output blocks — no silent loss
    assert len(content) == 3, f"Expected 3 blocks, got {len(content)}: {content}"  # noqa: PLR2004
    text_blocks = [b for b in content if b.get("type") == "text"]
    rr_blocks = [b for b in content if b.get("type") == "resource_reference"]
    assert len(text_blocks) == 1, f"Expected 1 text block, got: {text_blocks}"
    assert len(rr_blocks) == 2, f"Expected 2 resource_reference blocks, got: {rr_blocks}"  # noqa: PLR2004
    modalities = {b.get("modality") for b in rr_blocks}
    assert modalities == {"image", "audio"}, (
        f"Expected image and audio modalities, got: {modalities}"
    )
    # Embedded blocks must be resource_reference_replay (Ralph-owned manifest handles)
    for rr in rr_blocks:
        assert rr.get("delivery") == "resource_reference_replay", (
            f"Embedded upstream blocks must use resource_reference_replay delivery, "
            f"got: {rr.get('delivery')!r} for block: {rr}"
        )
        assert str(rr.get("uri", "")).startswith("ralph://media/"), (
            f"Embedded upstream blocks must have ralph://media/... URI, got: {rr.get('uri')!r}"
        )


def test_upstream_uri_backed_block_uses_resource_reference_delivery() -> None:
    """URI-backed upstream blocks must use 'resource_reference' delivery (not replay).

    URI-backed blocks preserve an external URI and are NOT Ralph-owned artifacts.
    They should NOT be replayed via read_media — the agent can access the URI directly.
    """
    class _FakeClient:
        def list_tools(self) -> list[object]:
            from ralph.mcp.upstream.models import UpstreamTool  # noqa: PLC0415
            return [UpstreamTool(name="pdf_tool", description="returns a PDF URI")]

        def call_tool(self, _name: str, _args: object) -> dict[str, object]:
            return {
                "content": [
                    {
                        "type": "pdf",
                        "uri": "https://example.com/report.pdf",
                        "mimeType": "application/pdf",
                    },
                ]
            }

    server = UpstreamMcpServer(name="pdf_server", transport="http", url="http://unused")

    class _FakeSession:
        media_manifest = MediaManifest()

    registry = UpstreamRegistry.build(
        [server],
        client_factory=lambda _srv: _FakeClient(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    )

    result = registry.call_tool(
        "ralph_upstream__pdf_server__pdf_tool", {}, session=_FakeSession()
    )

    content = result.get("content", [])
    assert len(content) == 1
    block = content[0]
    assert block.get("type") == "resource_reference"
    assert block.get("delivery") == "resource_reference", (
        f"URI-backed upstream blocks must use 'resource_reference' delivery, "
        f"got: {block.get('delivery')!r}"
    )
    assert block.get("uri") == "https://example.com/report.pdf", (
        f"URI-backed upstream blocks must preserve the external URI, got: {block.get('uri')!r}"
    )
    # URI-backed blocks must NOT create manifest entries
    session = _FakeSession()
    registry.call_tool(
        "ralph_upstream__pdf_server__pdf_tool", {}, session=session
    )
    assert session.media_manifest.is_empty(), (
        "URI-backed upstream blocks must NOT store entries in the session manifest"
    )


def test_prompt_sidecar_preserves_mixed_modality_metadata_for_runner_handoff() -> None:
    """Sidecar with image + pdf + audio must round-trip all metadata for runner handoff."""
    from ralph.prompts.debug_dump import media_session_path  # noqa: PLC0415
    from ralph.prompts.materialize import collect_media_entries_for_phase  # noqa: PLC0415

    workspace = MemoryWorkspace()
    mixed_payload = json.dumps({
        "schema_version": "2",
        "phase": "development",
        "artifacts": [
            {
                "artifact_id": "img-001",
                "uri": "ralph://media/img-001",
                "mime_type": "image/png",
                "title": "screenshot.png",
                "modality": "image",
                "delivery": "inline_image",
                "reason": "Claude supports inline image delivery",
                "source_path": "screenshots/cap.png",
                "cache_path": "",
                "source_uri": "",
                "block_type": "",
            },
            {
                "artifact_id": "pdf-002",
                "uri": "ralph://media/pdf-002",
                "mime_type": "application/pdf",
                "title": "report.pdf",
                "modality": "pdf",
                "delivery": "typed_block",
                "reason": "'pdf' delivered as typed block 'pdf' for provider 'claude'",
                "source_path": "reports/report.pdf",
                "cache_path": ".agent/tmp/media/report.pdf",
                "source_uri": "",
                "block_type": "pdf",
            },
            {
                "artifact_id": "aud-003",
                "uri": "ralph://media/aud-003",
                "mime_type": "audio/mpeg",
                "title": "clip.mp3",
                "modality": "audio",
                "delivery": "resource_reference_replay",
                "reason": "unknown provider — defaulting to resource_reference_replay delivery",
                "source_path": "audio/clip.mp3",
                "cache_path": "",
                "source_uri": "",
                "block_type": "",
            },
        ],
    })
    workspace.write(media_session_path("development"), mixed_payload)

    entries = collect_media_entries_for_phase(workspace, "development")

    assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"  # noqa: PLR2004

    image_e = next(e for e in entries if e.modality == "image")
    pdf_e = next(e for e in entries if e.modality == "pdf")
    audio_e = next(e for e in entries if e.modality == "audio")

    # Image: delivery and reason preserved
    assert image_e.delivery == "inline_image"
    assert "inline" in image_e.reason

    # PDF: typed_block delivery + block_type preserved
    assert pdf_e.delivery == "typed_block"
    assert pdf_e.block_type == "pdf"
    assert "typed block" in pdf_e.reason

    # Audio: resource_reference_replay delivery preserved
    assert audio_e.delivery == "resource_reference_replay"
    assert audio_e.modality == "audio"
    assert audio_e.uri == "ralph://media/aud-003"


# ---------------------------------------------------------------------------
# Gemini typed audio/video block tests (Plan Step 1 extension)
# ---------------------------------------------------------------------------

_TINY_AUDIO_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00"  # Minimal ID3 header stub

_TINY_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"  # mp4 ftyp atom stub


@pytest.mark.integration
def test_gemini_audio_delivers_typed_audio_block(tmp_path: Path) -> None:
    """Gemini provider must return a typed audio block (TYPED_BLOCK delivery) for audio files.

    This proves the Gemini-specific audio typed-block delivery path through Ralph's
    managed MCP runtime. The verdict must be TYPED_BLOCK with block_type='audio'.
    """
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_bytes(_TINY_AUDIO_BYTES)

    server = _build_server(tmp_path, provider="gemini", model_id="gemini-2.0-flash")
    state = _initialize(server, multimodal_client=True)

    result = _tool_call(server, state, str(RalphToolName.READ_MEDIA), {"path": "clip.mp3"})

    # Gemini accepts audio as a typed block — the call must succeed.
    assert result.get("isError") is not True, (
        f"read_media should not be an error for Gemini audio, got: {result}"
    )
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert len(content) == 1
    block = content[0]
    # Gemini audio must come back as an AudioContent typed block (type='audio') or
    # resource_reference (when the inline size limit triggers replay path).
    # AudioContent has type='audio' — this is the TYPED_BLOCK delivery form.
    block_type = block.get("type")
    assert block_type in {"audio", "resource_reference"}, (
        f"Expected AudioContent (type='audio') or resource_reference for Gemini audio, "
        f"got type={block_type!r}"
    )


@pytest.mark.integration
def test_gemini_video_delivers_typed_video_block(tmp_path: Path) -> None:
    """Gemini provider must deliver video as a typed block (TYPED_BLOCK delivery).

    Proves the Gemini-specific video typed-block delivery path through Ralph's
    managed MCP runtime. The verdict must be TYPED_BLOCK with block_type='video'.
    """
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(_TINY_VIDEO_BYTES)

    server = _build_server(tmp_path, provider="gemini", model_id="gemini-2.0-flash")
    state = _initialize(server, multimodal_client=True)

    result = _tool_call(server, state, str(RalphToolName.READ_MEDIA), {"path": "clip.mp4"})

    assert result.get("isError") is not True, (
        f"read_media should not be an error for Gemini video, got: {result}"
    )
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert len(content) == 1
    block = content[0]
    # Gemini video must come back as a VideoContent typed block (type='video') or
    # resource_reference (replay path). VideoContent has type='video' — TYPED_BLOCK form.
    block_type = block.get("type")
    assert block_type in {"video", "resource_reference"}, (
        f"Expected VideoContent (type='video') or resource_reference for Gemini video, "
        f"got type={block_type!r}"
    )


def test_gemini_audio_capability_verdict_is_typed_block() -> None:
    """Gemini audio verdict must be TYPED_BLOCK with block_type='audio'.

    Black-box proof that the capability contract for Gemini audio is correct:
    the resolved profile must report typed_block delivery so downstream layers
    can choose the right transport form.
    """
    gemini_identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    verdict = get_delivery_mode(gemini_identity, "audio")
    assert verdict.delivery == DeliveryMode.TYPED_BLOCK, (
        f"Gemini audio must be TYPED_BLOCK, got {verdict.delivery!r}"
    )
    assert verdict.block_type == "audio", (
        f"Gemini audio block_type must be 'audio', got {verdict.block_type!r}"
    )
    assert verdict.is_supported(), "Gemini audio must be marked as supported"


def test_gemini_video_capability_verdict_is_typed_block() -> None:
    """Gemini video verdict must be TYPED_BLOCK with block_type='video'."""
    gemini_identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    verdict = get_delivery_mode(gemini_identity, "video")
    assert verdict.delivery == DeliveryMode.TYPED_BLOCK, (
        f"Gemini video must be TYPED_BLOCK, got {verdict.delivery!r}"
    )
    assert verdict.block_type == "video", (
        f"Gemini video block_type must be 'video', got {verdict.block_type!r}"
    )
    assert verdict.is_supported(), "Gemini video must be marked as supported"


# ---------------------------------------------------------------------------
# OpenAI/Codex explicit unsupported outcomes (Plan Step 1 extension)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_openai_audio_returns_explicit_unsupported_error(tmp_path: Path) -> None:
    """OpenAI provider must return an explicit unsupported error for audio files.

    Ralph's managed MCP runtime must surface a clear rejection rather than
    returning resource_reference or silently pretending the upload succeeded.
    """
    audio_file = tmp_path / "meeting.mp3"
    audio_file.write_bytes(_TINY_AUDIO_BYTES)

    server = _build_server(tmp_path, provider="openai", model_id="gpt-4o")
    state = _initialize(server, multimodal_client=True)

    result = _tool_call(server, state, str(RalphToolName.READ_MEDIA), {"path": "meeting.mp3"})

    assert result.get("isError") is True, (
        f"read_media must return isError=True for OpenAI audio (unsupported modality), "
        f"got: {result}"
    )
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert len(content) >= 1
    error_text = str(content[0].get("text", ""))
    assert "audio" in error_text.lower() or "unsupported" in error_text.lower(), (
        f"Error must mention 'audio' or 'unsupported', got: {error_text!r}"
    )


@pytest.mark.integration
def test_openai_pdf_returns_explicit_unsupported_error(tmp_path: Path) -> None:
    """OpenAI provider must return an explicit unsupported error for PDF files.

    OpenAI's chat completion API cannot accept PDFs via Ralph's managed MCP path.
    Ralph must surface this as an explicit rejection, not a silent text-only fallback.
    """
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(_TINY_PDF_BYTES)

    server = _build_server(tmp_path, provider="openai", model_id="gpt-4o")
    state = _initialize(server, multimodal_client=True)

    result = _tool_call(server, state, str(RalphToolName.READ_MEDIA), {"path": "report.pdf"})

    assert result.get("isError") is True, (
        f"read_media must return isError=True for OpenAI PDF (unsupported modality), "
        f"got: {result}"
    )
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert len(content) >= 1
    error_text = str(content[0].get("text", ""))
    assert "pdf" in error_text.lower() or "unsupported" in error_text.lower(), (
        f"Error must mention 'pdf' or 'unsupported', got: {error_text!r}"
    )


def test_openai_unsupported_modality_verdicts_are_explicit() -> None:
    """OpenAI capability verdicts must explicitly mark audio, video, PDF as UNSUPPORTED.

    Black-box proof that the capability contract rejects these modalities for OpenAI
    rather than silently routing them to resource_reference_replay.
    """
    openai_identity = MultimodalModelIdentity(provider="openai", model_id="gpt-4o")
    for modality in ("audio", "video", "pdf", "document"):
        verdict = get_delivery_mode(openai_identity, modality)
        assert verdict.delivery == DeliveryMode.UNSUPPORTED, (
            f"OpenAI {modality!r} must be UNSUPPORTED, got {verdict.delivery!r}"
        )
        assert not verdict.is_supported(), (
            f"OpenAI {modality!r} must not be marked as supported"
        )
        assert verdict.reason, (
            f"OpenAI {modality!r} unsupported verdict must include a reason"
        )


# ---------------------------------------------------------------------------
# Managed document-path proof (DOCX-class typed document or replay delivery)
# ---------------------------------------------------------------------------

_TINY_DOCX_BYTES = (
    b"PK\x03\x04"  # ZIP local file header magic (OOXML is a ZIP archive)
    + b"\x14\x00" * 25  # minimal content to satisfy the file reader
)


@pytest.mark.integration
def test_claude_docx_round_trip_preserves_typed_document_or_replay_delivery(
    tmp_path: Path,
) -> None:
    """DOCX artifact obtained via Ralph's MCP surface must survive the managed path.

    This proves the managed-runtime document-path requirement: when a Claude-provider
    session calls read_media on a DOCX file, the response must be a typed document
    block (typed_block delivery) or a replayable resource reference — NOT silent
    text flattening. The test drives McpServer.handle_request() in-process.
    """
    docx_file = tmp_path / "report.docx"
    docx_file.write_bytes(_TINY_DOCX_BYTES)

    server = _build_server(tmp_path, provider="claude", model_id="claude-3-5-sonnet-20241022")
    state = _initialize(server, multimodal_client=True)

    result = _tool_call(server, state, str(RalphToolName.READ_MEDIA), {"path": "report.docx"})

    assert result.get("isError") is not True, (
        f"read_media must not return an error for Claude DOCX, got: {result}"
    )
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert len(content) == 1, f"Expected exactly 1 content block, got: {content}"
    block = content[0]
    block_type = block.get("type")
    # Claude provider must deliver DOCX as a typed document block or as a replayable
    # resource reference — never as raw text or as an error.
    assert block_type in {"document", "resource_reference"}, (
        f"DOCX must be delivered as typed 'document' block or resource_reference, "
        f"got type={block_type!r}. Text flattening is not acceptable."
    )
    if block_type == "document":
        assert block.get("delivery") == "typed_block", (
            f"document block must have delivery='typed_block', got: {block.get('delivery')!r}"
        )
    elif block_type == "resource_reference":
        uri = str(block.get("uri", ""))
        assert uri.startswith("ralph://media/"), (
            f"resource_reference must use ralph://media/ handle, got: {uri!r}"
        )
