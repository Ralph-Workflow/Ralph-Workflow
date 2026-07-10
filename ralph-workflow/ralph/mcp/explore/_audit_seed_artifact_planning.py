"""Per-family audit seed entries for the artifact_planning family group.

Extracted from :mod:`ralph.mcp.explore.audit_register` so the hub
module stays under the per-file line ceiling. Each tuple is merged
into :data:`ralph.mcp.explore.audit_register.AUDIT_REGISTER` at
module load via the per-family aggregate.

AC-06: each entry keeps its full rationale, counters, and risk
fields so the audit-register registry can validate measured
provenance end-to-end.
"""

from __future__ import annotations

from ralph.mcp.explore.audit_register import (
    AuditEntry,
    AuditFamily,
    AuditOutcome,
    _counters,
)
from ralph.mcp.tools.names import RalphToolName

_SEED_ARTIFACT_PLANNING: tuple[AuditEntry, ...] = (
    AuditEntry(
        tool=RalphToolName.SUBMIT_ARTIFACT,
        family=AuditFamily.ARTIFACT,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_submit_artifact already returns per-field "
            "ValidationError envelopes via ``PlanArtifactValidationError`` "
            "with ``code`` and ``repair`` pointers. The structured "
            "behavior matches the Phase-4 acceptance contract for "
            "artifact tools; no rework is required. Baseline counters "
            "are pinned on the Phase 0 measurement fixtures."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=384,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.SUBMIT_PLAN_SECTION,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_submit_plan_section already returns per-section "
            "validation_warnings via ``PlanArtifactValidationError`` "
            "with ``code`` + ``repair`` pointers. The structured "
            "behavior matches the Phase-4 acceptance contract for "
            "planning tools; no rework is required."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=320,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.SUBMIT_PLAN_SECTIONS,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_submit_plan_sections is the batch variant of "
            "ralph_submit_plan_section; same per-section "
            "validation_warnings contract applies. No rework "
            "required for Phase 4."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=320,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.INSERT_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_insert_plan_step returns the reindex echo payload "
            "with validation_warnings + code + repair on validation "
            "failure. The structured behavior matches the Phase-4 "
            "acceptance contract for planning tools."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.REPLACE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_replace_plan_step returns the reindex echo payload "
            "with structured validation_warnings + code + repair "
            "on validation failure. No rework required."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.REMOVE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_remove_plan_step returns the reindex echo payload "
            "with structured validation_warnings on validation "
            "failure. No rework required."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=192,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.MOVE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_move_plan_step returns the reindex echo payload "
            "with structured validation_warnings on validation "
            "failure. No rework required."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=192,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.PATCH_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_patch_step returns the reindex echo payload "
            "with structured validation_warnings on validation "
            "failure. No rework required."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.FINALIZE_PLAN,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_finalize_plan returns the full validation summary "
            "with structured code + repair pointers on failure. The "
            "structured behavior matches the Phase-4 acceptance "
            "contract for planning tools."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=384,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.GET_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_get_plan_draft returns the staged draft plus a "
            "validation summary; no compactness rework required. "
            "The tool already returns bounded JSON."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=192,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DISCARD_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_discard_plan_draft returns a bounded confirmation "
            "envelope; no rework required."
        ),
        counters=_counters(
            transcript_tokens=48,
            returned_bytes=96,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.VALIDATE_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "ralph_validate_draft runs the full read-only validator "
            "and returns structured validation_warnings with code + "
            "repair pointers. The structured behavior matches the "
            "Phase-4 acceptance contract for planning tools."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
    ),
)
