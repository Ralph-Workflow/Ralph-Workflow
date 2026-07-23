"""Audit seed entries for markdown artifact MCP tools."""

from __future__ import annotations

from ralph.mcp.explore._audit_types import AuditEntry, AuditFamily, AuditOutcome, _counters
from ralph.mcp.tools.names import RalphToolName

_SEED_MARKDOWN_ARTIFACTS: tuple[AuditEntry, ...] = (
    AuditEntry(
        tool=RalphToolName.SUBMIT_MD_ARTIFACT,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale="Markdown artifact submission validates before canonical persistence.",
        counters=_counters(transcript_tokens=96, returned_bytes=384, tool_calls=1),
    ),
    AuditEntry(
        tool=RalphToolName.VERIFY_MD_ARTIFACT,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale="Markdown artifact verification is pure and returns bounded diagnostics.",
        counters=_counters(transcript_tokens=64, returned_bytes=256, tool_calls=1),
    ),
)

__all__ = ["_SEED_MARKDOWN_ARTIFACTS"]
