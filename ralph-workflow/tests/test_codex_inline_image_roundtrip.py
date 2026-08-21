"""Regression: the Codex CLI must never receive an inline image block.

Field evidence (2026-08-20, ``codex/gpt-5.6-terra`` dev loop): Ralph
answered a ``read_media`` call with a base64 ``ImageContent`` block.
The Codex CLI re-serialised that MCP tool result into its next
Responses API request using the part type ``output_text``, which the
API rejects::

    [400]: Invalid value: 'output_text'. Supported values are:
    'input_text', 'input_image', 'input_file', and 'scoped_content'.
    (param: input[101].output[1])

The turn died (``turn.failed``), the process was killed, and the run
was graded ``FAILED (no artifact)`` because ``development_result.md``
was never written. Ralph cannot fix the Codex CLI's serialisation, so
it must not hand that transport an inline image in the first place.

The delivery decision keys on the *transport*, not the provider: a
Codex CLI run resolves to ``provider='openai'`` with
``transport='codex'`` (see ``_TRANSPORT_FIXED_PROVIDER`` in
``ralph/mcp/session_plan.py``), and the defect is in the CLI's wire
serialisation rather than in any provider's API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE, ResourceReferenceContent
from ralph.mcp.multimodal.capabilities import (
    DeliveryMode,
    MultimodalModelIdentity,
    get_delivery_mode,
    inline_image_roundtrip_unsafe,
)
from ralph.mcp.session_plan import resolve_model_identity
from ralph.mcp.tools.coordination import ImageContent
from ralph.mcp.tools.workspace._media_handlers import (
    handle_read_image,
    handle_read_media,
)
from ralph.workspace.fs import FsWorkspace
from tests.mock_session_with_manifest import MockSessionWithManifest

MEDIA_READ_CAPABILITY = "media.read"

pytestmark = pytest.mark.timeout_seconds(5)

# Minimal valid PNG: 1x1 transparent pixel, generated inline so the
# test stays hermetic (no file fixtures).
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDAT"
    b"\x78\x9c\x62\x00\x01\x00\x00\x05\x00\x01"
    b"\x0d\x0a\x2d\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_CODEX_IDENTITY = MultimodalModelIdentity(
    provider="openai",
    model_id="gpt-5.6-terra",
    transport="codex",
)


def _write_png(tmp_path: Path, name: str = "tiny.png") -> Path:
    file_path = tmp_path / name
    file_path.write_bytes(_PNG_BYTES)
    return file_path


def _set_profile(session: object, profile: object) -> None:
    """Inject a capability profile onto a mock session.

    The production path stores the profile on the session; the mock
    exposes the same attribute so the production helper reads it.
    """
    session.capability_profile = profile


def _codex_session() -> MockSessionWithManifest:
    return MockSessionWithManifest(
        MEDIA_READ_CAPABILITY,
        model_identity=_CODEX_IDENTITY,
    )


# ---------------------------------------------------------------------------
# Capability layer — the single source of truth for delivery decisions
# ---------------------------------------------------------------------------


def test_codex_transport_is_flagged_inline_image_roundtrip_unsafe() -> None:
    """The Codex CLI cannot round-trip an inline MCP image block."""
    assert inline_image_roundtrip_unsafe(_CODEX_IDENTITY)


def test_codex_transport_image_verdict_is_not_inline_image() -> None:
    """A Codex-transport image resolves to resource-reference delivery."""
    verdict = get_delivery_mode(_CODEX_IDENTITY, MODALITY_IMAGE)

    assert verdict.delivery is DeliveryMode.RESOURCE_REFERENCE_REPLAY
    assert verdict.delivery is not DeliveryMode.INLINE_IMAGE


def test_codex_transport_image_verdict_reason_names_the_transport() -> None:
    """The verdict explains why inline was withheld, for operator triage."""
    verdict = get_delivery_mode(_CODEX_IDENTITY, MODALITY_IMAGE)

    assert "codex" in verdict.reason.lower()


def test_openai_provider_without_codex_transport_still_gets_inline_image() -> None:
    """The guard keys on transport: a direct OpenAI identity is unaffected."""
    identity = MultimodalModelIdentity(provider="openai", model_id="gpt-5.6-terra")

    assert not inline_image_roundtrip_unsafe(identity)
    assert get_delivery_mode(identity, MODALITY_IMAGE).delivery is DeliveryMode.INLINE_IMAGE


def test_claude_transport_still_gets_inline_image() -> None:
    """No regression for the transports that do round-trip images."""
    identity = MultimodalModelIdentity(
        provider="claude",
        model_id="claude-opus-5",
        transport="claude",
    )

    assert not inline_image_roundtrip_unsafe(identity)
    assert get_delivery_mode(identity, MODALITY_IMAGE).delivery is DeliveryMode.INLINE_IMAGE


def test_resolved_codex_identity_is_roundtrip_unsafe() -> None:
    """The identity the runtime actually builds for Codex trips the guard.

    Guards the provider/transport mismatch that made the ``"codex"``
    key in ``_PROVIDER_UNSUPPORTED_MODALITIES`` dead code: a Codex CLI
    run resolves to ``provider='openai'``, so a provider-keyed check
    would never fire.
    """
    identity = resolve_model_identity(AgentTransport.CODEX, "gpt-5.6-terra")

    assert identity.transport == "codex"
    assert inline_image_roundtrip_unsafe(identity)


# ---------------------------------------------------------------------------
# Tool layer — the surface that produced the 400
# ---------------------------------------------------------------------------


def test_read_media_on_codex_returns_no_inline_image_block(tmp_path: Path) -> None:
    """The exact regression: no ImageContent reaches a Codex transport."""
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    assert result.is_error is False
    assert not any(isinstance(block, ImageContent) for block in result.content)


def test_read_image_on_codex_returns_no_inline_image_block(tmp_path: Path) -> None:
    """``read_image`` shares the delivery path and must be guarded too."""
    _write_png(tmp_path)

    result = handle_read_image(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    assert result.is_error is False
    assert not any(isinstance(block, ImageContent) for block in result.content)


def test_read_media_on_codex_returns_replayable_resource_reference(
    tmp_path: Path,
) -> None:
    """Withholding inline bytes must not lose the artifact."""
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    refs = [b for b in result.content if isinstance(b, ResourceReferenceContent)]
    assert len(refs) == 1
    assert refs[0].uri.startswith("ralph://media/")
    assert refs[0].modality == MODALITY_IMAGE


def test_codex_replay_of_media_uri_never_returns_inline_image(tmp_path: Path) -> None:
    """Replaying the handle must not re-introduce the inline block.

    Without this the guard would be a single-hop fix: the first read
    returns a handle, and dereferencing that handle hands Codex the
    ImageContent that kills the turn.
    """
    _write_png(tmp_path)
    session = _codex_session()
    workspace = FsWorkspace(tmp_path)

    first = handle_read_media(session, workspace, {"path": "tiny.png"})
    refs = [b for b in first.content if isinstance(b, ResourceReferenceContent)]
    assert refs, "expected a replay handle from the first read"

    replayed = handle_read_media(session, workspace, {"path": refs[0].uri})

    assert replayed.is_error is False
    assert not any(isinstance(block, ImageContent) for block in replayed.content)


def test_codex_metadata_format_registers_a_replay_handle(tmp_path: Path) -> None:
    """``format='metadata'`` must still hand Codex a dereferenceable handle.

    The metadata path skipped artifact registration for images because
    images were previously always inline-delivered; a Codex image is
    resource-reference delivered, so the handle must be registered.
    """
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png", "format": "metadata"},
    )

    envelope = json.loads(result.content[0].text)

    assert envelope["media_kind"] == "image"
    assert envelope["resource_handle"] is not None
    assert envelope["resource_handle"].startswith("ralph://media/")


def test_non_codex_transport_still_receives_inline_image(tmp_path: Path) -> None:
    """Negative case: the fix must not degrade image-capable transports."""
    _write_png(tmp_path)

    result = handle_read_media(
        MockSessionWithManifest(
            MEDIA_READ_CAPABILITY,
            model_identity=MultimodalModelIdentity(
                provider="claude",
                model_id="claude-opus-5",
                transport="claude",
            ),
        ),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    assert result.is_error is False
    assert any(isinstance(block, ImageContent) for block in result.content)


def test_codex_receives_a_warning_explaining_the_withheld_image(
    tmp_path: Path,
) -> None:
    """A bare reference for an obvious PNG needs a stated reason.

    Criterion 3's graceful-degradation contract: the agent gets a usable
    payload AND an operator-visible explanation of why this is the
    degraded path.
    """
    _write_png(tmp_path)

    result = handle_read_media(
        _codex_session(),
        FsWorkspace(tmp_path),
        {"path": "tiny.png"},
    )

    warnings = [
        block.text
        for block in result.content
        if getattr(block, "type", None) == "text" and "WARNING" in getattr(block, "text", "")
    ]
    assert len(warnings) == 1, result.content
    text = warnings[0].lower()
    assert "codex" in text
    # The message must not send the agent round a loop for bytes it
    # cannot be given.
    assert "do not retry" in text
    assert "metadata" in text




# ---------------------------------------------------------------------------
# Runtime plumbing — the guard is worthless if the transport never arrives
# ---------------------------------------------------------------------------


def test_a_restricted_transport_gets_no_typed_blocks_either() -> None:
    """A typed PDF block is the same construct as the image block.

    The measured failure is the CLI re-serialising a Ralph-minted
    non-standard content block into its own API request. The modality
    differs between an image block and a pdf block; the hazard does not.
    Inference from the measured case, in the conservative direction --
    the artifact still arrives as a resource reference.
    """
    from ralph.mcp.multimodal.artifacts import MODALITY_DOCUMENT, MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity, get_delivery_mode

    codex_with_claude_model = MultimodalModelIdentity(
        provider="anthropic", model_id="claude-opus-5", transport="codex"
    )

    for modality in (MODALITY_PDF, MODALITY_DOCUMENT):
        assert get_delivery_mode(codex_with_claude_model, modality).delivery is (
            DeliveryMode.RESOURCE_REFERENCE_REPLAY
        ), modality


def test_an_unrestricted_transport_still_gets_typed_blocks() -> None:
    """The withholding is transport-scoped, not a blanket downgrade."""
    from ralph.mcp.multimodal.artifacts import MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity, get_delivery_mode

    claude = MultimodalModelIdentity(
        provider="anthropic", model_id="claude-opus-5", transport="claude"
    )

    assert get_delivery_mode(claude, MODALITY_PDF).delivery is DeliveryMode.TYPED_BLOCK


def test_a_reference_block_never_claims_a_typed_delivery(tmp_path: Path) -> None:
    """A reference block must carry a reference delivery.

    A verdict can name a block type nothing knows how to build --
    ``profile_from_payload`` never validates it -- and that falls
    through to a resource reference. Stamping the verdict's own
    ``typed_block`` on it contradicts the block's contract and makes the
    artifact invisible to both handoff extractors, which filter on the
    two reference values.
    """
    from ralph.mcp.multimodal.artifacts import MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import (
        CapabilityVerdict,
        MultimodalModelIdentity,
        ResolvedCapabilityProfile,
    )

    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    claude = MultimodalModelIdentity(
        provider="claude", model_id="claude-opus-5", transport="claude"
    )
    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY, model_identity=claude)
    _set_profile(
        session,
        ResolvedCapabilityProfile(
            identity=claude,
            verdicts={
                MODALITY_PDF: CapabilityVerdict(
                    modality=MODALITY_PDF,
                    delivery=DeliveryMode.TYPED_BLOCK,
                    provider="claude",
                    model_id="claude-opus-5",
                    reason="fresh",
                    block_type="a-block-type-nothing-builds",
                )
            },
        ),
    )

    result = handle_read_media(session, FsWorkspace(tmp_path), {"path": "doc.pdf"})

    refs = [b for b in result.content if isinstance(b, ResourceReferenceContent)]
    assert refs, result.content
    assert refs[0].delivery is DeliveryMode.RESOURCE_REFERENCE_REPLAY


def test_a_stored_typed_block_verdict_cannot_outrank_the_transport() -> None:
    """A persisted verdict may not promise more than the identity allows.

    Correcting only ``inline_image`` left the same hole one modality
    over: a stored ``pdf -> typed_block`` handed a Ralph-minted typed
    block to a CLI that cannot carry one.
    """
    from ralph.mcp.multimodal.artifacts import MODALITY_PDF
    from ralph.mcp.multimodal.capabilities import (
        CapabilityVerdict,
        MultimodalModelIdentity,
        ResolvedCapabilityProfile,
    )

    codex_with_claude_model = MultimodalModelIdentity(
        provider="anthropic", model_id="claude-opus-5", transport="codex"
    )
    profile = ResolvedCapabilityProfile(
        identity=codex_with_claude_model,
        verdicts={
            MODALITY_PDF: CapabilityVerdict(
                modality=MODALITY_PDF,
                delivery=DeliveryMode.TYPED_BLOCK,
                provider="anthropic",
                model_id="claude-opus-5",
                reason="stored before the guard existed",
                block_type="pdf",
            )
        },
    )

    assert profile.verdict_for(MODALITY_PDF).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )
    # ...and the correction survives re-serialisation.
    verdicts = profile.to_payload()["verdicts"]
    assert isinstance(verdicts, dict)
    assert verdicts[MODALITY_PDF]["delivery"] == DeliveryMode.RESOURCE_REFERENCE_REPLAY.value


def test_a_more_conservative_stored_verdict_is_respected() -> None:
    """The correction runs one way only; deliberate restraint is kept."""
    from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE
    from ralph.mcp.multimodal.capabilities import (
        UNKNOWN_IDENTITY,
        CapabilityVerdict,
        ResolvedCapabilityProfile,
    )

    profile = ResolvedCapabilityProfile(
        identity=UNKNOWN_IDENTITY,
        verdicts={
            MODALITY_IMAGE: CapabilityVerdict(
                modality=MODALITY_IMAGE,
                delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
                provider="unknown",
                model_id=None,
                reason="a payload value sanitised to something safe",
            )
        },
    )

    assert profile.verdict_for(MODALITY_IMAGE).delivery is (
        DeliveryMode.RESOURCE_REFERENCE_REPLAY
    )
