"""Per-family audit seed entries for the git_process family group.

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

_SEED_GIT_PROCESS: tuple[AuditEntry, ...] = (
    AuditEntry(
        tool=RalphToolName.GIT_STATUS,
        family=AuditFamily.GIT_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "AC-11: ``format=compact`` returns a bounded summary card "
            "with changed-path ranking and unchanged-path elision. Raw "
            "behavior preserved for the default format."
        ),
        counters=_counters(
            transcript_tokens=64,
            returned_bytes=256,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.GIT_DIFF,
        family=AuditFamily.GIT_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "AC-11: ``format=summary`` returns a bounded card with "
            "changed files, plus per-file added/removed totals. Raw "
            "diff behavior preserved for the default format."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=512,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.GIT_LOG,
        family=AuditFamily.GIT_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='summary'`` returns a compact JSON envelope "
            "``{format, count, commits:[{short_sha, sha, subject}], "
            "bytes_in, bytes_out}`` for one bounded call. Default "
            "``format='raw'`` preserves the legacy ``git log -<count> "
            "--oneline`` output byte-for-byte so existing callers are "
            "unaffected. Byte-savings on Phase 4 fixtures are >=30 "
            "percent versus the raw shape."
        ),
        counters=_counters(
            transcript_tokens=80,
            returned_bytes=320,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.GIT_SHOW,
        family=AuditFamily.GIT_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='summary'`` returns a compact header-only "
            "JSON envelope ``{format, ref, kind, sha, short_sha, "
            "author_name, author_email, author_date, subject, parents, "
            "bytes_in, bytes_out, truncated:false}`` without the patch "
            "body. Default ``format='raw'`` preserves the legacy "
            "``git show <ref>`` output byte-for-byte so existing "
            "callers are unaffected. Byte-savings on Phase 4 fixtures "
            "are >=30 percent versus the raw shape."
        ),
        counters=_counters(
            transcript_tokens=64,
            returned_bytes=256,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.EXEC,
        family=AuditFamily.PROCESS,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "AC-11: ``format=summary`` returns a bounded JSON envelope "
            "with separate ``stdout_resource_id`` / ``stderr_resource_id`` "
            "replayable handles, per-stream spill paths, and head/tail "
            "previews. The bounded-timeout contract is preserved and "
            "raw behavior remains the default."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=768,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.UNSAFE_EXEC,
        family=AuditFamily.PROCESS,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Unsafe exec is unbounded by design; AC-11 summary mode is "
            "only on the bounded exec path. The unsafe variant keeps "
            "its current shape; the bounded exec summary is the "
            "production remediation."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=768,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.RAW_EXEC,
        family=AuditFamily.PROCESS,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Raw exec is the unfiltered escape hatch; the AC-11 summary "
            "mode is intentionally only on the bounded exec tool. The "
            "raw variant keeps its current shape so a single audit "
            "register can preserve the capability surface."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=768,
            tool_calls=1,
        ),
    ),
)
