"""Per-tool audit register for every Ralph-owned MCP tool.

This module owns a typed, immutable register with one entry per
``RalphToolName`` member. Each entry records the audit outcome
(``keep`` / ``add_argument`` / ``rework_internals`` / ``defer``), a
rationale, and baseline research-gate counters (transcript tokens,
returned bytes, tool calls, evidence recall).

Outcomes are seeded from the Phase 0 architecture finding audit
section. They can be updated by later phases after measurement
proves a deferral is unjustified or a ``keep`` requires rework.

The module is a pure data module with no I/O so it is fully black-box
testable. Tests in ``tests/test_explore_audit_register.py`` assert
that:

* Every ``RalphToolName`` member has exactly one entry.
* Every ``defer`` entry has a non-empty rationale.
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
    """Baseline research-gate counters for a single MCP tool.

    All counters are nullable so a deferred audit can record the
    rationale without yet pinning numeric baselines. Numeric baselines
    are filled by Phase 0 measurement scripts.
    """

    transcript_tokens: int | None = None
    returned_bytes: int | None = None
    tool_calls: int | None = None
    evidence_recall: float | None = None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """A single audit entry: tool outcome, rationale, and counters."""

    tool: RalphToolName
    family: AuditFamily
    outcome: AuditOutcome
    rationale: str
    counters: AuditCounters | None = None

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
    ),
    AuditEntry(
        tool=RalphToolName.WRITE_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Write path is author-controlled; the index marks dirty paths "
            "after a successful write rather than changing write semantics."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.LIST_DIRECTORY,
        family=AuditFamily.WORKSPACE_LIST,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact/ranked/outline views gated on Phase-2 symbol data; "
            "raw listing is cheap and the current 'no-arg + recursive' "
            "contract is already minimal."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.LIST_DIRECTORY_RECURSIVE,
        family=AuditFamily.WORKSPACE_LIST,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as list_directory: deferred to Phase 2 because the ranked "
            "view needs symbol/heading counts that Phase 1 does not extract."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DIRECTORY_TREE,
        family=AuditFamily.WORKSPACE_LIST,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Outline + symbol counts require Phase-2 structure extraction; "
            "raw tree is already minimal enough to defer."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.SEARCH_FILES,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Add ranked/role/changed_only/return_evidence_ids so agents stop "
            "walking 1000-path matches; ranking uses path/FTS/role/git-changed "
            "components only in Phase 1 (symbol/graph disabled)."
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
    ),
    AuditEntry(
        tool=RalphToolName.LIST_ALLOWED_ROOTS,
        family=AuditFamily.WORKSPACE_READ,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Configuration surface; no per-call optimization is appropriate."
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
    ),
    AuditEntry(
        tool=RalphToolName.APPEND_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Append is a thin wrapper; dirty-path marking is wired in Phase 1."
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
    ),
    AuditEntry(
        tool=RalphToolName.MOVE_FILE,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Move touches both src and dest; Phase-1 dirty marking handles "
            "both paths in a single call. No additional argument required."
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
    ),
    AuditEntry(
        tool=RalphToolName.DELETE_PATH,
        family=AuditFamily.WORKSPACE_MUTATE,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Delete marks the path dirty so reindex can write a tombstone; "
            "no agent-visible argument change needed."
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
    ),
    AuditEntry(
        tool=RalphToolName.GIT_LOG,
        family=AuditFamily.GIT_READ,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact log cards are Phase 4; current git log is already "
            "bounded and not a primary agent hot path."
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
    ),
    AuditEntry(
        tool=RalphToolName.SUBMIT_ARTIFACT,
        family=AuditFamily.ARTIFACT,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact validation errors and exact repair pointers are "
            "Phase 4. Phase 1 leaves the artifact contract untouched."
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
    ),
    AuditEntry(
        tool=RalphToolName.SUBMIT_PLAN_SECTIONS,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.INSERT_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.REPLACE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.REMOVE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.MOVE_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.PATCH_PLAN_STEP,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.FINALIZE_PLAN,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.GET_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DISCARD_PLAN_DRAFT,
        family=AuditFamily.PLANNING,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as submit_plan_section; Phase 4 remediation."
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
    ),
    AuditEntry(
        tool=RalphToolName.REPORT_PROGRESS,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Shorter status payloads are Phase 4 non-index remediation. "
            "Coordination contract is currently adequate."
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
    ),
    AuditEntry(
        tool=RalphToolName.COORDINATE,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Cross-agent coordination channel; Phase 4 handles "
            "structured-field refactor."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.READ_ENV,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Single-string env read; current output is already bounded."
        ),
    ),
    AuditEntry(
        tool=RalphToolName.WEB_SEARCH,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Compact summaries and replayable resource handles are Phase 4."
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
    ),
    AuditEntry(
        tool=RalphToolName.DOWNLOAD_URL,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Same as visit_url; Phase 4 non-index remediation."
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
    ),
    AuditEntry(
        tool=RalphToolName.READ_MEDIA,
        family=AuditFamily.MEDIA,
        outcome=AuditOutcome.DEFER,
        rationale=(
            "Replayable resource handles for media are Phase 4."
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
