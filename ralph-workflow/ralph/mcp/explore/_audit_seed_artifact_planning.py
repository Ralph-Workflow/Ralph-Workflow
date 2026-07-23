"""Audit seed entries for the markdown-only artifact tool family."""

from __future__ import annotations

from ralph.mcp.explore._audit_types import AuditEntry, AuditFamily, AuditOutcome, _counters
from ralph.mcp.tools.names import RalphToolName

_SEED_ARTIFACT_PLANNING: tuple[AuditEntry, ...] = (
    AuditEntry(
        tool=RalphToolName.SUBMIT_MD_ARTIFACT,
        family=AuditFamily.ARTIFACT,
        outcome=AuditOutcome.KEEP,
        rationale="Markdown submission uses the shared line-anchored validator.",
        counters=_counters(transcript_tokens=128, returned_bytes=256, tool_calls=1),
    ),
    AuditEntry(
        tool=RalphToolName.VERIFY_MD_ARTIFACT,
        family=AuditFamily.ARTIFACT,
        outcome=AuditOutcome.KEEP,
        rationale="Check-only validation calls the same markdown validator as submit.",
        counters=_counters(transcript_tokens=96, returned_bytes=192, tool_calls=1),
    ),
    AuditEntry(
        tool=RalphToolName.EDIT_MD_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.KEEP,
        rationale="Stable-ID plan editing replaces JSON draft mutations.",
        counters=_counters(transcript_tokens=96, returned_bytes=192, tool_calls=1),
    ),
)
