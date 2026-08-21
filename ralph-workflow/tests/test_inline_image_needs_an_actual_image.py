"""An ``INLINE_IMAGE`` verdict alone must not put bytes in an image block.

The verdict can arrive from a persisted profile, which is untrusted
input. A stored ``pdf -> inline_image`` sent a PDF's bytes inside an
``ImageContent`` block -- a malformed request that kills the turn in
exactly the way the incident this guard exists for did.

Both clauses of that guard -- the artifact must BE an image, and its
mime type must be one the inline union accepts -- were unpinned:
deleting either one left the suite green.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE, MODALITY_PDF
from ralph.mcp.multimodal.capabilities import (
    CapabilityVerdict,
    DeliveryMode,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
)
from ralph.mcp.tools.coordination import ImageContent
from ralph.mcp.tools.workspace._media_handlers import handle_read_media
from ralph.workspace.fs import FsWorkspace
from tests.mock_session_with_manifest import MockSessionWithManifest

_CLAUDE = MultimodalModelIdentity(
    provider="claude",
    model_id="claude-opus-5",
    transport="claude",
)
# A minimal but real PDF: the readers sniff the header to infer modality.
_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF\n"
_TIFF_BYTES = b"II*\x00" + b"\x00" * 60
_MEDIA_READ_CAPABILITY = "media.read"
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


def _poisoned_session(modality: str) -> MockSessionWithManifest:
    """A session whose stored profile promises inline delivery for ``modality``."""
    session = MockSessionWithManifest(_MEDIA_READ_CAPABILITY, model_identity=_CLAUDE)
    session.capability_profile = ResolvedCapabilityProfile(
        identity=_CLAUDE,
        verdicts={
            modality: CapabilityVerdict(
                modality=modality,
                delivery=DeliveryMode.INLINE_IMAGE,
                provider="claude",
                model_id="claude-opus-5",
                reason="a stored value promising inline delivery",
            )
        },
    )
    return session


def _image_blocks(result: object) -> list[object]:
    content = getattr(result, "content", [])
    return [block for block in content if isinstance(block, ImageContent)]


def _replay(session: MockSessionWithManifest, workspace: FsWorkspace, uri: str) -> object:
    return handle_read_media(session, workspace, {"path": uri})


def test_a_stored_inline_verdict_on_a_mislabelled_entry_mints_no_image_block(
    tmp_path: Path,
) -> None:
    """The artifact must actually BE an image, whatever the verdict says.

    The manifest is persisted, untrusted state like the profile: a row
    can name a modality and a mime type that disagree. This one claims
    ``pdf`` with an inline-capable mime, so only the MODALITY clause
    stands between a PDF's bytes and an ``ImageContent`` block.
    """
    session = _poisoned_session(MODALITY_PDF)
    entry = session.media_manifest.add(
        title="report.pdf",
        mime_type="image/png",
        modality=MODALITY_PDF,
        raw_bytes=_PDF_BYTES,
    )

    replayed = _replay(session, FsWorkspace(tmp_path), entry.uri)

    assert not _image_blocks(replayed)


def test_a_stored_inline_verdict_on_a_non_inline_mime_mints_no_image_block(
    tmp_path: Path,
) -> None:
    """The mime type must be one the inline image union actually accepts.

    A TIFF is an image by modality and cannot be carried inline, so the
    modality clause alone lets it straight through.
    """
    session = _poisoned_session(MODALITY_IMAGE)
    entry = session.media_manifest.add(
        title="scan.tiff",
        mime_type="image/tiff",
        modality=MODALITY_IMAGE,
        raw_bytes=_TIFF_BYTES,
    )

    replayed = _replay(session, FsWorkspace(tmp_path), entry.uri)

    assert not _image_blocks(replayed)


def test_a_genuine_inline_image_still_replays_inline(tmp_path: Path) -> None:
    """The guard must not be vacuous: a real PNG still comes back inline."""
    session = _poisoned_session(MODALITY_IMAGE)
    entry = session.media_manifest.add(
        title="tiny.png",
        mime_type="image/png",
        modality=MODALITY_IMAGE,
        raw_bytes=_PNG_BYTES,
    )

    replayed = _replay(session, FsWorkspace(tmp_path), entry.uri)

    assert _image_blocks(replayed)
