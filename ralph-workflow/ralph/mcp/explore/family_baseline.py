"""Per-family representative baseline flows for the audit register.

The architecture finding requires that every audited family have one
deterministic representative baseline-flow record so the indexed path
can be measured against the existing operation model without relying
on agent transcripts. Each record names the current-operation script
(the tool calls an agent would make today), the baseline counter
snapshot (transcript tokens, returned bytes, tool calls, recall),
and the catalog-token evidence (the rough schema-token cost of the
tools involved). The Phase 0 numbers are conservative so a future
re-measurement can only ever shrink them, never inflate them
silently.

Required family coverage (AC-01 / AC-04): read, list, search,
edit, git, artifact, exec. These are the seven families whose
existing tool flow was measured in Phase 0 and whose indexed path
(or compact/summary mode) is compared against that flow in the
benchmark gate.

This module lives apart from ``audit_register.py`` so the audit's
MCP-timeout scan (a full ``ast.parse`` + walk) does not have to
process the long literal strings in the baseline evidence
records; the baseline data is consumed only by the audit and
benchmark code paths, not by the MCP handlers.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.explore.audit_register import (
    AuditCounters,
    AuditFamily,
    _counters,
)


@dataclass(frozen=True, slots=True)
class FamilyBaselineFlow:
    """Deterministic representative baseline flow for a single family.

    The record is intentionally lightweight: name + tool script +
    baseline counters + catalog-token evidence. The benchmark harness
    may use this to assert the indexed flow beats the baseline on the
    same question without re-deriving the baseline from a live
    transcript.
    """

    family: AuditFamily
    name: str
    current_operation_script: tuple[str, ...]
    counters: AuditCounters
    catalog_token_evidence: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                f"FamilyBaselineFlow({self.family!r}): name must be non-empty"
            )
        if not self.current_operation_script:
            raise ValueError(
                f"FamilyBaselineFlow({self.family!r}): "
                "current_operation_script must list at least one tool"
            )
        if not self.catalog_token_evidence.strip():
            raise ValueError(
                f"FamilyBaselineFlow({self.family!r}): "
                "catalog_token_evidence must be non-empty"
            )


_FAMILY_BASELINE_FLOWS: tuple[FamilyBaselineFlow, ...] = (
    FamilyBaselineFlow(
        family=AuditFamily.WORKSPACE_READ,
        name="find_handler_test",
        current_operation_script=(
            "search_files",
            "grep_files",
            "read_file",
        ),
        counters=_counters(
            transcript_tokens=480,
            returned_bytes=3072,
            tool_calls=3,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
        catalog_token_evidence=(
            "search_files + grep_files + read_file schema tokens (Phase 0): "
            "~480 transcript tokens, ~3 KB returned bytes per scripted call."
        ),
    ),
    FamilyBaselineFlow(
        family=AuditFamily.WORKSPACE_LIST,
        name="list_directory_with_ranked_view",
        current_operation_script=(
            "list_directory",
            "list_directory_recursive",
        ),
        counters=_counters(
            transcript_tokens=352,
            returned_bytes=2432,
            tool_calls=2,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
        catalog_token_evidence=(
            "list_directory + list_directory_recursive schema tokens (Phase 0): "
            "~352 transcript tokens, ~2.4 KB returned bytes per scripted call."
        ),
    ),
    FamilyBaselineFlow(
        family=AuditFamily.WORKSPACE_SEARCH,
        name="search_for_symbol_or_path",
        current_operation_script=(
            "search_files",
            "grep_files",
            "read_file",
        ),
        counters=_counters(
            transcript_tokens=480,
            returned_bytes=3072,
            tool_calls=3,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
        catalog_token_evidence=(
            "search_files + grep_files + read_file schema tokens (Phase 0): "
            "~480 transcript tokens, ~3 KB returned bytes per scripted call. "
            "The search family baseline is the same shape as read; the "
            "indexed path adds ranked/role/changed_only score components."
        ),
    ),
    FamilyBaselineFlow(
        family=AuditFamily.WORKSPACE_MUTATE,
        name="targeted_edit_with_read_back",
        current_operation_script=(
            "read_file",
            "edit_file",
            "read_file",
        ),
        counters=_counters(
            transcript_tokens=320,
            returned_bytes=1536,
            tool_calls=3,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
        catalog_token_evidence=(
            "read_file + edit_file + read_file schema tokens (Phase 0): "
            "~320 transcript tokens, ~1.5 KB returned bytes per scripted call."
        ),
    ),
    FamilyBaselineFlow(
        family=AuditFamily.GIT_READ,
        name="inspect_change_set",
        current_operation_script=(
            "git_status",
            "git_diff",
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=768,
            tool_calls=2,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
        catalog_token_evidence=(
            "git_status + git_diff schema tokens (Phase 0): "
            "~192 transcript tokens, ~768 returned bytes per scripted call."
        ),
    ),
    FamilyBaselineFlow(
        family=AuditFamily.ARTIFACT,
        name="submit_development_result",
        current_operation_script=(
            "read_file",
            "ralph_submit_md_artifact",
        ),
        counters=_counters(
            transcript_tokens=376,
            returned_bytes=1024,
            tool_calls=2,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
        catalog_token_evidence=(
            "read_file + ralph_submit_md_artifact schema tokens (Phase 0): "
            "~376 transcript tokens, ~1 KB returned bytes per scripted call."
        ),
    ),
    FamilyBaselineFlow(
        family=AuditFamily.PROCESS,
        name="run_bounded_subprocess",
        current_operation_script=(
            "exec",
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=768,
            tool_calls=1,
            evidence_recall=1.0,
            evidence_precision=1.0,
        ),
        catalog_token_evidence=(
            "exec schema tokens (Phase 0): "
            "~192 transcript tokens, ~768 returned bytes per scripted call. "
            "The summary mode is a backward-compatible add_argument on the "
            "same tool; the unsafe_exec / raw_exec family members keep the "
            "raw shape and are kept as-is (KEEP audit outcomes)."
        ),
    ),
)


def family_baseline_flows() -> tuple[FamilyBaselineFlow, ...]:
    """Return the immutable per-family representative baseline flow."""
    return _FAMILY_BASELINE_FLOWS


__all__ = ["FamilyBaselineFlow", "family_baseline_flows"]
