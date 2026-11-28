from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import run_git
from ralph.project_policy import (
    refreshed_measurement_facts,
    refreshed_measurement_lines,
    volatile_citations_in_added_lines,
)
from ralph.project_policy.guard_downgrades import downgraded_guards
from ralph.project_policy.markers import CANONICAL_DIR
from ralph.project_policy.run_timestamps import run_timestamps_in_added_lines

if TYPE_CHECKING:
    from pathlib import Path

_SHOWN_FINDINGS = 10


def proposal_policy_churn(repo_root: Path, base: str, tree_sha: str) -> tuple[str, ...]:
    result = run_git(
        ("diff", "-U0", "--no-color", "--no-ext-diff", base, tree_sha, "--", CANONICAL_DIR),
        cwd=repo_root,
        label="commit-review:policy-churn-diff",
    )
    if result.returncode != 0 or not result.stdout:
        return ()
    diff = result.stdout
    return (
        *downgraded_guards(diff),
        *volatile_citations_in_added_lines(diff),
        *run_timestamps_in_added_lines(diff),
        *refreshed_measurement_facts(diff),
        *(f"{path}: {new[:80]}" for path, _old, new in refreshed_measurement_lines(diff)),
    )


def render_policy_churn_reason(findings: tuple[str, ...]) -> str:
    shown = "; ".join(findings[:_SHOWN_FINDINGS])
    more = len(findings) - _SHOWN_FINDINGS
    suffix = f"; and {more} more" if more > 0 else ""
    return (
        f"The proposal weakens or churns the policy corpus ({len(findings)} finding(s)): "
        f"{shown}{suffix}. A guard whose Enforcement cell is deterministic stays "
        "deterministic: fix or declare its enforcing command instead of downgrading it to "
        "advisory. Policy states durable rules and verified facts, not the latest "
        "run: cite a stage name and a dated, durable source instead of a worktree-local or "
        "append-only record (tmp/, .agent/, a log, an *.ndjson ledger), record a run as a "
        "calendar date rather than a run timestamp, and never re-record a measured fact, "
        "timing, count or status from a newer run. Remove these edits and keep the rule."
    )


__all__ = ["proposal_policy_churn", "render_policy_churn_reason"]
