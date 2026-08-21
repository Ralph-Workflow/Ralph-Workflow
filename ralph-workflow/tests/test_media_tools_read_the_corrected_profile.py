"""The media tools must resolve delivery through the CALLER's profile.

``_get_session_capability_profile`` prefers ``caller_capability_profile``
over the session's raw ``capability_profile``. That preference is the
only thing keeping the tools on the CORRECTED profile: the caller
property re-bases a stored profile onto the authoritative identity, and
the raw field is whatever a payload happened to contain.

A mutation sweep reverted that one line and the whole media suite stayed
green -- while a session payload that is legal on disk today handed a
codex caller its image bytes.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.multimodal.artifacts import MODALITY_IMAGE
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
#: The shape a flagless codex run produces: transport known, provider not.
_CODEX = MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport="codex")


def test_a_stale_stored_profile_cannot_reopen_the_inline_path(tmp_path: Path) -> None:
    """The identity says codex; only the stored profile disagrees.

    This payload is legal on disk: ``model_identity`` carries the
    transport and the ``capability_profile`` beside it was serialised
    before that was known, so its own transport is ``None`` and its
    stored image verdict still says ``inline_image``. The tools must
    resolve against the caller's corrected profile, not the stale one.
    """
    (tmp_path / "tiny.png").write_bytes(_PNG_BYTES)
    session = MockSessionWithManifest(_MEDIA_READ_CAPABILITY, model_identity=_CODEX)
    session.capability_profile = ResolvedCapabilityProfile(
        identity=MultimodalModelIdentity(provider="openai", model_id="gpt-5", transport=None),
        verdicts={
            MODALITY_IMAGE: CapabilityVerdict(
                modality=MODALITY_IMAGE,
                delivery=DeliveryMode.INLINE_IMAGE,
                provider="openai",
                model_id="gpt-5",
                reason="serialised before the CLI was known",
            )
        },
    )

    result = handle_read_media(session, FsWorkspace(tmp_path), {"path": "tiny.png"})

    assert result.is_error is False
    assert not [b for b in result.content if isinstance(b, ImageContent)], result.content


def test_a_capable_caller_still_gets_its_inline_image(tmp_path: Path) -> None:
    """Not vacuous: the same path still delivers to a CLI that can carry it."""
    (tmp_path / "tiny.png").write_bytes(_PNG_BYTES)
    claude = MultimodalModelIdentity(
        provider="claude", model_id="claude-opus-5", transport="claude"
    )
    session = MockSessionWithManifest(_MEDIA_READ_CAPABILITY, model_identity=claude)

    result = handle_read_media(session, FsWorkspace(tmp_path), {"path": "tiny.png"})

    assert [b for b in result.content if isinstance(b, ImageContent)], result.content
