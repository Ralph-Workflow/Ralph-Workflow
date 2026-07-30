from __future__ import annotations

import json

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    resolve_capability_profile,
)
from ralph.prompts.debug_dump import media_session_path
from ralph.prompts.materialize import (
    collect_media_entries_for_phase,
)
from ralph.workspace.memory import MemoryWorkspace


class _ArtifactSubmitSession:
    session_id = "test-session"
    drain = "planning_analysis"

    def check_capability(self, capability: str) -> object:
        return capability == "artifact.submit"


MINIMAL_PLAN_HANDOFF = (
    "# Execution Plan\n\n"
    "1. Add regression coverage.\n"
    "2. Tighten non-planning prompt preconditions.\n"
)


def _write_plan_handoff(workspace: MemoryWorkspace) -> None:
    workspace.write(".agent/PLAN.md", MINIMAL_PLAN_HANDOFF)


def test_sidecar_entries_built_from_capability_profile_verdicts_preserve_all_metadata() -> None:
    """Capability-profile-derived metadata flows correctly through the media session index.

    When the MCP workspace tool writes a media session index using verdicts from
    resolve_capability_profile, collect_media_entries_for_phase must read those entries
    back with delivery, block_type, reason, and URI all intact.
    This proves the end-to-end data contract from capability detection to runner handoff.
    """

    # Claude profile: image=inline_image, pdf=typed_block, audio=unsupported
    claude_identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet")
    profile = resolve_capability_profile(claude_identity)

    image_v = profile.verdict_for("image")
    pdf_v = profile.verdict_for("pdf")
    audio_v = profile.verdict_for("audio")

    # Write the media session index in the same format the MCP workspace tool uses.
    # This simulates what _write_media_session_entry produces after a read_media call.
    index_payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "cap-img-001",
                    "uri": "ralph://media/cap-img-001",
                    "mime_type": "image/png",
                    "title": "cap.png",
                    "modality": "image",
                    "delivery": image_v.delivery.value,
                    "reason": image_v.reason,
                    "source_path": "screens/cap.png",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": image_v.block_type or "",
                },
                {
                    "artifact_id": "cap-pdf-002",
                    "uri": "ralph://media/cap-pdf-002",
                    "mime_type": "application/pdf",
                    "title": "spec.pdf",
                    "modality": "pdf",
                    "delivery": pdf_v.delivery.value,
                    "reason": pdf_v.reason,
                    "source_path": "docs/spec.pdf",
                    "cache_path": ".agent/tmp/media/spec.pdf",
                    "source_uri": "",
                    "block_type": pdf_v.block_type or "",
                },
                {
                    "artifact_id": "cap-aud-003",
                    "uri": "ralph://media/cap-aud-003",
                    "mime_type": "audio/mpeg",
                    "title": "clip.mp3",
                    "modality": "audio",
                    "delivery": audio_v.delivery.value,
                    "reason": audio_v.reason,
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": audio_v.block_type or "",
                },
            ],
        }
    )

    workspace = MemoryWorkspace()
    workspace.write(media_session_path("development"), index_payload)

    reloaded = collect_media_entries_for_phase(workspace, "development")
    by_modality = {e.modality: e for e in reloaded}
    assert set(by_modality) == {"image", "pdf", "audio"}

    img = by_modality["image"]
    assert img.delivery == "inline_image", f"Image must be inline_image, got {img.delivery!r}"
    assert img.uri == "ralph://media/cap-img-001"
    assert img.reason, "Image entry must carry non-empty reason from capability verdict"

    pdf = by_modality["pdf"]
    assert pdf.delivery == "typed_block", f"PDF must be typed_block, got {pdf.delivery!r}"
    assert pdf.block_type == "pdf", f"PDF block_type must be 'pdf', got {pdf.block_type!r}"
    assert pdf.reason, "PDF entry must carry non-empty reason from capability verdict"
    assert pdf.source_path == "docs/spec.pdf"

    aud = by_modality["audio"]
    assert aud.delivery == "unsupported", (
        f"Audio must be unsupported for Claude, got {aud.delivery!r}"
    )
    assert aud.reason, "Unsupported audio entry must carry non-empty reason"
    assert aud.block_type == "", "Unsupported audio must have empty block_type"


def test_collect_media_entries_preserves_failure_kind_through_sidecar_round_trip() -> None:
    """failure_kind must survive JSON serialization and reload without re-inference.

    Writing a session index entry with failure_kind='unsupported_runtime_seam' and
    reloading via collect_media_entries_for_phase must yield the same value, keeping
    unsupported_runtime_seam distinct from unsupported_modality all the way to invoke time.
    """

    payload = json.dumps(
        {
            "schema_version": "2",
            "phase": "development",
            "artifacts": [
                {
                    "artifact_id": "seam-fail-001",
                    "uri": "ralph://media/seam-fail-001",
                    "mime_type": "video/mp4",
                    "title": "clip.mp4",
                    "modality": "video",
                    "delivery": "unsupported",
                    "reason": "Active runtime seam cannot carry video through the handoff path",
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                    "failure_kind": "unsupported_runtime_seam",
                },
                {
                    "artifact_id": "modality-fail-002",
                    "uri": "ralph://media/modality-fail-002",
                    "mime_type": "audio/mpeg",
                    "title": "clip.mp3",
                    "modality": "audio",
                    "delivery": "unsupported",
                    "reason": "Provider does not support audio",
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": "",
                    "block_type": "",
                    "failure_kind": "unsupported_modality",
                },
            ],
        }
    )

    workspace = MemoryWorkspace()
    workspace.write(media_session_path("development"), payload)

    entries = collect_media_entries_for_phase(workspace, "development")

    assert len(entries) == 2
    by_modality = {e.modality: e for e in entries}

    video_e = by_modality["video"]
    assert video_e.failure_kind == "unsupported_runtime_seam", (
        f"failure_kind must survive sidecar round-trip, got: {video_e.failure_kind!r}"
    )
    assert video_e.delivery == "unsupported"

    audio_e = by_modality["audio"]
    assert audio_e.failure_kind == "unsupported_modality", (
        "unsupported_modality failure_kind must survive sidecar round-trip, "
        f"got: {audio_e.failure_kind!r}"
    )
    assert audio_e.delivery == "unsupported"


def test_collect_media_entries_dedupes_repeated_identity_key() -> None:

    workspace = MemoryWorkspace()
    workspace.write(
        media_session_path("development"),
        json.dumps(
            {
                "schema_version": "2",
                "phase": "development",
                "artifacts": [
                    {
                        "artifact_id": "old-001",
                        "uri": "ralph://media/old-001",
                        "mime_type": "application/pdf",
                        "title": "report.pdf",
                        "modality": "pdf",
                        "delivery": "resource_reference_replay",
                        "reason": "first",
                        "source_path": "docs/report.pdf",
                        "cache_path": ".agent/tmp/media/old-001",
                        "source_uri": "",
                        "block_type": "",
                        "failure_kind": "",
                        "identity_key": "source-path:pdf:docs/report.pdf",
                    },
                    {
                        "artifact_id": "new-002",
                        "uri": "ralph://media/new-002",
                        "mime_type": "application/pdf",
                        "title": "report.pdf",
                        "modality": "pdf",
                        "delivery": "resource_reference_replay",
                        "reason": "second",
                        "source_path": "docs/report.pdf",
                        "cache_path": ".agent/tmp/media/new-002",
                        "source_uri": "",
                        "block_type": "",
                        "failure_kind": "",
                        "identity_key": "source-path:pdf:docs/report.pdf",
                    },
                ],
            }
        ),
    )

    entries = collect_media_entries_for_phase(workspace, "development")

    assert len(entries) == 1
    assert entries[0].artifact_id == "new-002"
    assert entries[0].identity_key == "source-path:pdf:docs/report.pdf"
