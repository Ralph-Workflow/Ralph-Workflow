"""Per-family audit seed entries for the workspace family group.

Extracted from :mod:`ralph.mcp.explore.audit_register` so the hub
module stays under the per-file line ceiling. Each tuple is merged
into :data:`ralph.mcp.explore.audit_register.AUDIT_REGISTER` at
module load via the per-family aggregate.

AC-06: each entry keeps its full rationale, counters, and risk
fields so the audit-register registry can validate measured
provenance end-to-end.
"""

from __future__ import annotations

from ralph.mcp.explore._audit_types import (
    AuditEntry,
    AuditFamily,
    AuditOutcome,
    _counters,
)
from ralph.mcp.tools.names import RalphToolName

_SEED_WORKSPACE: tuple[AuditEntry, ...] = (
    AuditEntry(
        tool=RalphToolName.READ_FILE,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Add evidence_id / span_id / symbol selectors with content-hash "
            "preconditions so agents stop reading entire files when an indexed "
            "span or symbol lookup suffices. The contract ships via the "
            "structure rows; absent evidence surfaces structured "
            "'unknown_evidence' / 'stale_evidence' responses rather than a "
            "deferred placeholder."
        ),
        counters=_counters(
            transcript_tokens=120,
            returned_bytes=512,
            tool_calls=1,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.READ_MULTIPLE_FILES,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Allow mixed evidence/span/symbol items so a single call replaces "
            "many read_file calls; Phase-1 evidence items work, span/symbol "
            "items return structured phase-2 fallback."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=1024,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.STAT_PATH,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "stat_path is metadata-only and already returns "
            "type/size/timestamps; adding indexed arguments would not reduce "
            "agent decisions."
        ),
        counters=_counters(
            transcript_tokens=48,
            returned_bytes=128,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.LIST_ALLOWED_ROOTS,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Configuration surface; no per-call optimization is appropriate."
        ),
        counters=_counters(
            transcript_tokens=24,
            returned_bytes=64,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.GREP_FILES,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Add use_index + rank_by + return_evidence_ids + dedupe_by_symbol "
            "+ max_snippet_lines so indexed literal/phrase queries return "
            "ranked evidence handles. Eligibility contract preserves live "
            "grep semantics for regex/lookaround/backreferences."
        ),
        counters=_counters(
            transcript_tokens=288,
            returned_bytes=1536,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.RALPH_REINDEX,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Phase 1 ships the new tool with a bounded changed/full "
            "refresh; the schema is compact and stable so no rework is "
            "required in this slice."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
            changed_file_count=0,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.RALPH_GRAPH,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 2/3 ships ralph_graph with prompt-exact neighbors, "
            "path, impact, hubs, and tests queries. Capability is "
            "WorkspaceRead; every response carries confidence, "
            "provenance, missing_data, freshness, truncation, and "
            "evidence_ids."
        ),
        counters=_counters(
            transcript_tokens=256,
            returned_bytes=1024,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.SEARCH_FILES,
        family=AuditFamily.WORKSPACE_SEARCH,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Add ranked/role/changed_only/return_evidence_ids so agents stop "
            "walking 1000-path matches; ranking uses path/FTS/role/git-changed "
            "components only in Phase 1 (symbol/graph disabled)."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=768,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.LIST_DIRECTORY,
        family=AuditFamily.WORKSPACE_LIST,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "AC-09: add ``view=raw|compact|ranked|outline``, "
            "``include_counts``, ``include_symbols``, ``changed_only``, "
            "``limit_children``, and ``use_index=auto|always|never``. "
            "Compact/ranked/outline views are wired through the indexed "
            "handle in workspace/_read_handlers.py and the Phase-2 "
            "structure data is consumed when available. Raw/default "
            "behavior is preserved and the audit register now reflects "
            "the shipped behavior (the previous DEFER outcome was "
            "stale once the ranked view landed)."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=384,
            tool_calls=1,
            stale_fallback_events=0,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.LIST_DIRECTORY_RECURSIVE,
        family=AuditFamily.WORKSPACE_LIST,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Same as list_directory: the recursive sibling shares the "
            "compact/ranked/outline views and use_index fallbacks. The "
            "previous DEFER outcome is stale because the implementation "
            "in workspace/_read_handlers.py already routes through the "
            "indexed handle."
        ),
        counters=_counters(
            transcript_tokens=256,
            returned_bytes=2048,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DIRECTORY_TREE,
        family=AuditFamily.WORKSPACE_LIST,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "AC-09: add ``view=raw|compact|ranked|outline``, "
            "``include_counts``, ``include_symbols``, ``changed_only``, "
            "``limit_children``, and ``use_index=auto|always|never``. "
            "directory_tree consumes the same Phase-2 structure data as "
            "list_directory; the previous DEFER outcome is stale now "
            "that the ranked/outline view is implemented and routed "
            "through the indexed handle."
        ),
        counters=_counters(
            transcript_tokens=384,
            returned_bytes=4096,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.WRITE_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Write path is author-controlled; the index marks dirty paths "
            "after a successful write rather than changing write semantics."
        ),
        counters=_counters(
            transcript_tokens=80,
            returned_bytes=192,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.EDIT_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase-1 adds expected_content_hash + reindex='auto' for safety. "
            "target/match_strategy/impact_preview are Phase 3 because impact "
            "needs ralph_graph neighbors/paths."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=512,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.APPEND_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Append is a thin wrapper; dirty-path marking is wired in Phase 1."
        ),
        counters=_counters(
            transcript_tokens=72,
            returned_bytes=192,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.CREATE_DIRECTORY,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "mkdirs is cheap and not agent-loop hot; dirty marking covers "
            "new directory contents downstream."
        ),
        counters=_counters(
            transcript_tokens=32,
            returned_bytes=64,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.MOVE_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Move touches both src and dest; Phase-1 dirty marking handles "
            "both paths in a single call. No additional argument required."
        ),
        counters=_counters(
            transcript_tokens=64,
            returned_bytes=128,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.COPY_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Copy touches dest only; Phase-1 dirty marking handles the new "
            "path. Content-hash reuse during reindex is internal."
        ),
        counters=_counters(
            transcript_tokens=64,
            returned_bytes=128,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DELETE_PATH,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Delete marks the path dirty so reindex can write a tombstone; "
            "no agent-visible argument change needed."
        ),
        counters=_counters(
            transcript_tokens=48,
            returned_bytes=96,
            tool_calls=1,
        ),
    ),
)
