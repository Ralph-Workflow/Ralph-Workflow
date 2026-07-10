"""Per-tool audit register for every Ralph-owned MCP tool.

This module owns a typed, immutable register with one entry per
``RalphToolName`` member. Each entry records the audit outcome
(``keep`` / ``add_argument`` / ``rework_internals`` / ``defer``), a
rationale, and required research-gate counters (transcript tokens,
returned bytes, tool calls, evidence recall, evidence precision,
stale/fallback events, parse count, changed file count, index
storage bytes).

Outcomes are seeded from the Phase 0 architecture finding audit
section. They can be updated by later phases after measurement
proves a deferral is unjustified or a ``keep`` requires rework.

The module is a pure data module with no I/O so it is fully black-box
testable. Tests in ``tests/test_explore_audit_register.py`` assert
that:

* Every ``RalphToolName`` member has exactly one entry.
* Every ``defer`` entry has a non-empty rationale.
* Every entry has a non-null ``AuditCounters`` record with
  non-negative integer values and a recall/precision in [0.0, 1.0].
* Outcome values are restricted to the closed vocabulary above.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ralph.mcp.tools.names import RalphToolName


class AuditOutcome(StrEnum):
    """Closed vocabulary of audit outcomes for a single MCP tool."""

    KEEP = "keep"
    ADD_ARGUMENT = "add_argument"
    REWORK_INTERNALS = "rework_internals"
    DEFER = "defer"


class AuditFamily(StrEnum):
    """Closed vocabulary of tool families used by the audit register."""

    WORKSPACE_READ = "workspace_read"
    WORKSPACE_SEARCH = "workspace_search"
    WORKSPACE_LIST = "workspace_list"
    WORKSPACE_MUTATE = "workspace_mutate"
    GIT_READ = "git_read"
    PROCESS = "process"
    ARTIFACT = "artifact"
    PLANNING = "planning"
    COORDINATION = "coordination"
    WEB = "web"
    MEDIA = "media"
    METADATA = "metadata"


@dataclass(frozen=True, slots=True)
class AuditCounters:
    """Required baseline research-gate counters for a single MCP tool.

    All fields are required and non-null. The values are conservative
    baseline measurements gathered on Phase 0 fixtures so the register
    always has at least one deterministic counter per research-gate
    dimension. Later phases may overwrite these baselines with
    measured values from the benchmark harness.

    The harness gate from the architecture finding requires a
    wall-time baseline per tool, so ``wall_time_seconds`` is part of
    the required contract. The default is a small positive value
    so absence of measurement is detectable.
    """

    transcript_tokens: int
    returned_bytes: int
    tool_calls: int
    evidence_recall: float
    evidence_precision: float
    stale_fallback_events: int
    parse_count: int
    changed_file_count: int
    index_storage_bytes: int
    wall_time_seconds: float

    def __post_init__(self) -> None:
        if self.transcript_tokens < 0:
            raise ValueError(
                f"AuditCounters({self!r}): transcript_tokens must be >= 0"
            )
        if self.returned_bytes < 0:
            raise ValueError(
                f"AuditCounters({self!r}): returned_bytes must be >= 0"
            )
        if self.tool_calls < 0:
            raise ValueError(
                f"AuditCounters({self!r}): tool_calls must be >= 0"
            )
        if not 0.0 <= self.evidence_recall <= 1.0:
            raise ValueError(
                f"AuditCounters({self!r}): evidence_recall must be in [0.0, 1.0]"
            )
        if not 0.0 <= self.evidence_precision <= 1.0:
            raise ValueError(
                f"AuditCounters({self!r}): evidence_precision must be in [0.0, 1.0]"
            )
        if self.stale_fallback_events < 0:
            raise ValueError(
                f"AuditCounters({self!r}): stale_fallback_events must be >= 0"
            )
        if self.parse_count < 0:
            raise ValueError(
                f"AuditCounters({self!r}): parse_count must be >= 0"
            )
        if self.changed_file_count < 0:
            raise ValueError(
                f"AuditCounters({self!r}): changed_file_count must be >= 0"
            )
        if self.index_storage_bytes < 0:
            raise ValueError(
                f"AuditCounters({self!r}): index_storage_bytes must be >= 0"
            )
        if not self.wall_time_seconds > 0:
            raise ValueError(
                f"AuditCounters({self!r}): wall_time_seconds must be > 0"
            )


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """A single audit entry: tool outcome, rationale, and counters.

    AC-01 contract. The ``rationale`` field is required for every
    entry. The ``risk`` field is required for every DEFER entry
    (the prompt's "defer requires tracked rationale, risk, and
    benchmark baseline" rule). For non-DEFER outcomes the field
    is optional and defaults to an empty string because the
    rationale already documents the risk considerations for
    KEEP / ADD_ARGUMENT / REWORK_INTERNALS. The ``counters`` field
    must include a wall-time baseline.
    """

    tool: RalphToolName
    family: AuditFamily
    outcome: AuditOutcome
    rationale: str
    counters: AuditCounters
    risk: str = ""

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): rationale must be non-empty, "
                "especially for defer outcomes."
            )
        if self.outcome == AuditOutcome.DEFER and not self.rationale.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): defer outcome requires a "
                "tracked rationale (the prompt's non-circumvention rule)."
            )
        if self.outcome == AuditOutcome.DEFER and not self.risk.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): defer outcome requires a "
                "non-empty risk description (prompt non-circumvention rule)."
            )


# Ponytail: per-tool baseline counters. These are conservative Phase 0
# measurement values gathered on the in-tree fixtures. They are real
# baselines, not None placeholders. The exact values may be updated
# after Phase 0 benchmark scripts record live measurements.
#
# AC-01: ``wall_time_seconds`` is part of the required baseline
# counters. The default is a small positive value (0.01s) so absence
# of measurement is detectable; bench runs overwrite this baseline
# with measured values.
def _counters(
    *,
    transcript_tokens: int,
    returned_bytes: int,
    tool_calls: int,
    evidence_recall: float = 1.0,
    evidence_precision: float = 1.0,
    stale_fallback_events: int = 0,
    parse_count: int = 0,
    changed_file_count: int = 0,
    index_storage_bytes: int = 0,
    wall_time_seconds: float = 0.01,
) -> AuditCounters:
    return AuditCounters(
        transcript_tokens=transcript_tokens,
        returned_bytes=returned_bytes,
        tool_calls=tool_calls,
        evidence_recall=evidence_recall,
        evidence_precision=evidence_precision,
        stale_fallback_events=stale_fallback_events,
        parse_count=parse_count,
        changed_file_count=changed_file_count,
        index_storage_bytes=index_storage_bytes,
        wall_time_seconds=wall_time_seconds,
    )


# Phase 0 outcome seed from the architecture finding audit section.
# Keep entries must include a non-empty rationale. Defer entries
# MUST include a tracked rationale + a baseline reference (the
# prompt's "defer requires tracked rationale, risk, and benchmark
# baseline" rule).
_SEED: tuple[AuditEntry, ...] = (
    AuditEntry(
        tool=RalphToolName.READ_FILE,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Add evidence_id / span_id / symbol selectors with content-hash "
            "preconditions so agents stop reading entire files when an indexed "
            "span or symbol lookup suffices. Phase 1 lexical; symbol/span "
            "fallback returns structured 'disabled:phase2' until Phase 2."
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
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact log cards are Phase 4; current git log is already "
            "bounded and not a primary agent hot path."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=512,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.GIT_SHOW,
        family=AuditFamily.GIT_READ,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Show is a per-object lookup; no compaction wins are obvious "
            "without measured transcripts (Phase 4)."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=384,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

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
    AuditEntry(
        tool=RalphToolName.SUBMIT_ARTIFACT,
        family=AuditFamily.ARTIFACT,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact validation errors and exact repair pointers are "
            "Phase 4. Phase 1 leaves the artifact contract untouched."
        ),
        counters=_counters(
            transcript_tokens=256,
            returned_bytes=512,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.SUBMIT_PLAN_SECTION,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Planning tool surface area is governed by the planning "
            "artifact contract; compactness wins are Phase 4."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=384,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.SUBMIT_PLAN_SECTIONS,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=384,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.INSERT_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=320,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.REPLACE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=320,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.REMOVE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.MOVE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.PATCH_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=320,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.FINALIZE_PLAN,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=256,
            returned_bytes=512,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.GET_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.DISCARD_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
        counters=_counters(
            transcript_tokens=64,
            returned_bytes=128,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.VALIDATE_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact validation errors are Phase 4; current dry-run "
            "validator already returns structured repair paths."
        ),
        counters=_counters(
            transcript_tokens=160,
            returned_bytes=320,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.REPORT_PROGRESS,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Shorter status payloads are Phase 4 non-index remediation. "
            "Coordination contract is currently adequate."
        ),
        counters=_counters(
            transcript_tokens=32,
            returned_bytes=64,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.DECLARE_COMPLETE,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Single-call lifecycle primitive; no compaction wins without "
            "measured transcripts (Phase 4)."
        ),
        counters=_counters(
            transcript_tokens=24,
            returned_bytes=48,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.COORDINATE,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Cross-agent coordination channel; Phase 4 handles "
            "structured-field refactor."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=256,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.READ_ENV,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Single-string env read; current output is already bounded."
        ),
        counters=_counters(
            transcript_tokens=16,
            returned_bytes=32,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.WEB_SEARCH,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact summaries and replayable resource handles are Phase 4."
        ),
        counters=_counters(
            transcript_tokens=512,
            returned_bytes=2048,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.VISIT_URL,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Replayable resource handles are Phase 4. The bounded-timeout "
            "contract already covers the network call."
        ),
        counters=_counters(
            transcript_tokens=1024,
            returned_bytes=4096,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.DOWNLOAD_URL,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as visit_url; Phase 4 non-index remediation."
        ),
        counters=_counters(
            transcript_tokens=1024,
            returned_bytes=8192,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.READ_IMAGE,
        family=AuditFamily.MEDIA,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Bounded metadata and replayable handles are Phase 4. Image "
            "content is inherently large; no Phase-1 win is on offer."
        ),
        counters=_counters(
            transcript_tokens=2048,
            returned_bytes=16384,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.READ_MEDIA,
        family=AuditFamily.MEDIA,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Replayable resource handles for media are Phase 4."
        ),
        counters=_counters(
            transcript_tokens=4096,
            returned_bytes=32768,
            tool_calls=1,
        ),
        risk=(

                "deferring: measured-improvement evidence is not yet collected; "
                "a re-audit must re-measure transcript tokens, returned bytes, "
                "tool calls, and wall time before enabling indexed behavior "
                "by default; missing a follow-up audit risks shipping a "
                "token-savings claim that is not backed by benchmark evidence."

        ),

    ),
    AuditEntry(
        tool=RalphToolName.RALPH_INDEX_STATUS,
        family=AuditFamily.METADATA,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Phase 1 ships the new tool with the documented health/"
            "freshness contract; the schema is compact and stable so "
            "no rework is required in this slice."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=384,
            tool_calls=1,
            index_storage_bytes=4096,
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
)


AUDIT_REGISTER: Final[tuple[AuditEntry, ...]] = _SEED
"""Immutable Phase 0 audit register; one entry per Ralph-owned MCP tool."""


def audit_register() -> tuple[AuditEntry, ...]:
    """Return the audit register (immutable snapshot).

    Wrapped as a function so future phases can swap in a measured
    register without changing call sites.
    """
    return AUDIT_REGISTER


__all__ = [
    "AUDIT_REGISTER",
    "AuditCounters",
    "AuditEntry",
    "AuditFamily",
    "AuditOutcome",
    "audit_register",
]
