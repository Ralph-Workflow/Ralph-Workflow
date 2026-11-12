from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.explore.audit_register import (
    AuditCounters,
    AuditFamily,
    _counters,
)


@dataclass(frozen=True, slots=True)
class FamilyBaselineFlow:
    family: AuditFamily
    name: str
    current_operation_script: tuple[str, ...]
    counters: AuditCounters
    catalog_token_evidence: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(f"FamilyBaselineFlow({self.family!r}): name must be non-empty")
        if not self.current_operation_script:
            raise ValueError(
                f"FamilyBaselineFlow({self.family!r}): "
                "current_operation_script must list at least one tool"
            )
        if not self.catalog_token_evidence.strip():
            raise ValueError(
                f"FamilyBaselineFlow({self.family!r}): catalog_token_evidence must be non-empty"
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
        current_operation_script=("exec",),
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
    return _FAMILY_BASELINE_FLOWS


__all__ = ["FamilyBaselineFlow", "family_baseline_flows"]
