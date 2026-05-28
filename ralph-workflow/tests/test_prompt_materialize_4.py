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


PLANNING_EDIT_GET_DRAFT_TEXT = (
    "Use `ralph_get_plan_draft` to inspect the current finalized plan "
    "or staged draft before editing."
)
PLANNING_EDIT_DEFECT_SCOPE_TEXT = (
    "Before revising any section, classify the feedback scope as one of:"
)
PLANNING_EDIT_GLOBAL_REDERIVATION_TEXT = (
    "If any feedback item reveals repo-wide incompleteness, invalid inventory, incorrect paths, "
    "narrow verification, or prompt-to-plan traceability gaps, you MUST re-derive the plan"
)
PLANNING_EDIT_FINALIZE_TEXT = (
    "Use `ralph_finalize_plan` after revising the affected sections so "
    "the updated plan replaces the prior finalized plan."
)
PLANNING_EDIT_SELF_AUDIT_TEXT = "Before `ralph_finalize_plan`, perform this self-audit:"
PLANNING_EDIT_RISK_COVERAGE_TEXT = (
    "- Risk coverage: concrete risks, mitigations, and edge cases are represented"
)
PLANNING_EDIT_PARALLELIZATION_TEXT = (
    "- Parallelization safety: any parallel work remains disjoint, realistic, and policy-compliant"
)
PLANNING_EDIT_MAINTAINABILITY_TEXT = (
    "- Maintainability and handoff quality: the plan stays concise, "
    "non-redundant, and explicit for development handoff"
)
PLANNING_EDIT_SCOPE_INVALIDATION_TEXT = (
    "If the ORIGINAL REQUEST has repository-wide acceptance criteria and the current plan "
    "narrowed scope before running repository-wide discovery"
)
PLANNING_EDIT_DISCOVERY_FIRST_TEXT = (
    "replace the summary, scope, and early steps so Step 1 becomes repo-wide discovery"
)
PLANNING_EDIT_SCOPE_DERIVATION_TEXT = (
    "- Scope derivation: when the task is repo-wide, implementation scope comes from an "
    "explicit repo-wide discovery step rather than a guessed subsystem"
)
PLANNING_EDIT_PASS_TARGET_TEXT = (
    "Your target is to submit the strongest revised plan you can so the next planning-analysis pass"
)
PLANNING_EDIT_NO_KNOWN_GAPS_TEXT = (
    "Do not finalize a draft that still has any known unresolved analyzer finding"
)
PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_TEXT = (
    "If fixing one section changes the truth of another section, replace every dependent section"
)
PLANNING_EDIT_NEXT_ANALYZER_TEXT = (
    "Before finalizing, proactively search for any additional repo-grounded failure"
)
PLANNING_EDIT_SURFACED_BLOCKER_TEXT = (
    "If a canonical verification command or repo-wide audit already surfaces a blocker "
    "during replanning"
)
PLANNING_EDIT_RULE_CATEGORY_TEXT = (
    "When the ORIGINAL REQUEST imposes repo-wide structural rules, build a repo-wide inventory"
)
PLANNING_EDIT_NO_EXCEPTION_TEXT = (
    "Do not preserve prompt-violating tests, files, or workflows as justified exceptions"
)
PLANNING_EDIT_STARTING_POINT_TEXT = (
    "Treat the planning-analysis feedback as a starting point, not as the full list of issues"
)
PLANNING_EDIT_NOT_LOCAL_PATCH_TEXT = (
    "Do not localize your revision pass to only the sections explicitly cited by the analyzer"
)
PLANNING_EDIT_SELF_ANALYSIS_TEXT = (
    "You must perform your own repo-grounded analysis before finalizing"
)
PLANNING_EDIT_ISSUE_MAPPING_TEXT = (
    "Every analyzer issue must map to concrete revised sections or an explicit verified reason"
)
PLANNING_ANALYSIS_MCP_REMEDIATION_TEXT = (
    "When describing remediation, target the planner's MCP revision workflow"
)
PLANNING_ANALYSIS_SECTION_RESUBMIT_TEXT = (
    "Exact plan sections to resubmit via the MCP plan-edit tools."
)


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
