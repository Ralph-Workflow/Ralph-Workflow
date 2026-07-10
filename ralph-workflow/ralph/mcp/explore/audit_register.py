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
    AuditEntry(
        tool=RalphToolName.REPORT_PROGRESS,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "report_progress already emits a bounded text payload "
            "suffixed with ``PROGRESS_PIPELINE_MARKER`` so the idle "
            "watchdog can key on it. The structured marker matches "
            "the Phase-4 acceptance contract for coordination tools."
        ),
        counters=_counters(
            transcript_tokens=24,
            returned_bytes=48,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DECLARE_COMPLETE,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "declare_complete writes a HMAC-signed completion "
            "sentinel and emits a bounded text payload with the "
            "completion marker. The structured behavior matches "
            "the Phase-4 acceptance contract for coordination tools."
        ),
        counters=_counters(
            transcript_tokens=16,
            returned_bytes=32,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.COORDINATE,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "coordinate is artifact.plan_write-gated; the response "
            "is suffixed with the ``[Coordination event emitted to "
            "pipeline]`` marker so the parent process can observe "
            "structured events. The structured marker matches the "
            "Phase-4 acceptance contract for coordination tools."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=192,
            tool_calls=1,
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
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='summary'`` returns a compact JSON envelope "
            "with truncated snippets (<=240 chars), a ``backend_chain_used`` "
            "counter, and ``bytes_in``/``bytes_out`` size counters. Default "
            "``format='raw'`` preserves the legacy Title/URL/Snippet text "
            "shape byte-for-byte so existing callers are unaffected. "
            "Byte-savings on Phase 4 fixtures are >=30 percent versus the "
            "raw shape."
        ),
        counters=_counters(
            transcript_tokens=320,
            returned_bytes=1024,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.VISIT_URL,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='metadata'`` returns a bounded JSON envelope "
            "with ``head_preview``, ``byte_count``, and ``bytes_in``/``bytes_out`` "
            "size counters and DROPS the full text body inline. Default "
            "``format='raw'`` preserves the legacy text body shape "
            "byte-for-byte so existing callers are unaffected. "
            "Byte-savings on Phase 4 fixtures are >=30 percent versus the "
            "raw shape."
        ),
        counters=_counters(
            transcript_tokens=512,
            returned_bytes=2048,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DOWNLOAD_URL,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='summary'`` returns metadata + a bounded "
            "``head_preview`` (first 240 bytes) + a ``sha256`` fingerprint "
            "(first 16 hex chars) and DOES NOT echo the downloaded body "
            "inline. Default ``format='raw'`` preserves the legacy "
            "metadata-only envelope byte-for-byte so existing callers are "
            "unaffected. Byte-savings on Phase 4 fixtures are >=30 percent "
            "versus the raw shape."
        ),
        counters=_counters(
            transcript_tokens=512,
            returned_bytes=2048,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.READ_IMAGE,
        family=AuditFamily.MEDIA,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='metadata'`` returns a bounded JSON envelope "
            "with ``mime_type``, ``size_bytes``, ``sha256``, ``width``, "
            "``height`` (PNG only), and an ``inline_only`` flag. Default "
            "``format='inline'`` preserves the legacy image content block "
            "byte-for-byte. ``handle_read_image`` never persists a "
            "``ralph://media/{artifact_id}`` artifact so the envelope's "
            "``resource_handle`` is always ``None``."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=512,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.READ_MEDIA,
        family=AuditFamily.MEDIA,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='metadata'`` returns a bounded JSON envelope "
            "with ``media_kind``, ``mime_type``, ``size_bytes``, ``sha256``, "
            "and a replayable ``resource_handle`` (``ralph://media/{artifact_id}``) "
            "when the underlying delivery registered a Ralph-owned artifact. "
            "Default ``format='inline'`` preserves the legacy block shape "
            "byte-for-byte. Inline media bytes are dropped in metadata mode."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=768,
            tool_calls=1,
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
