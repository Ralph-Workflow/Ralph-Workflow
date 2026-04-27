"""Canonical workflow progress accounting.

This module owns all mutations for workflow progress fields, including completed
outer progress (``iteration`` and ``reviewer_pass``), inner analysis-loop
counters, routing budgets, review-issue flags tied to progress boundaries, and
checkpoint-facing progress mirrors derived from canonical state.

Contract:

* ``iteration`` counts completed development cycles only.
* ``reviewer_pass`` counts completed review passes only.
* Analysis loopbacks mutate only the inner loop counter for the current
  cycle or pass.
* Capped analysis loopback preserves outer progress and carries the inner
  loop counter to the cap until analysis or commit outcome resets it.
* Skipped commits route onward without incrementing outer progress, but they end
  the current inner loop and therefore reset the corresponding analysis counter.
* Checkpoint mirrors must derive directly from canonical ``PipelineState``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_DEVELOPMENT_ANALYSIS, PHASE_REVIEW
from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.pipeline.state import CommitState, PipelineState

if TYPE_CHECKING:
    from ralph.checkpoint.run_context import RunContext
    from ralph.config.enums import PipelinePhase
    from ralph.policy.models import PipelinePolicy


DEVELOPMENT_COMMIT_PHASE = "development_commit"
REVIEW_ANALYSIS_PHASE = "review_analysis"
REVIEW_COMMIT_PHASE = "review_commit"


def advance_phase(
    state: PipelineState,
    target_phase: PipelinePhase,
    *,
    policy: PipelinePolicy | None,
) -> PipelineState:
    """Advance phases while applying only canonical routing-budget bookkeeping."""
    updates: dict[str, object] = {
        "phase": target_phase,
        "previous_phase": state.phase,
        "last_agent_session_id": None,
        "session_preserve_retry_pending": False,
    }

    if target_phase in (DEVELOPMENT_COMMIT_PHASE, REVIEW_COMMIT_PHASE):
        updates["commit"] = CommitState()

    if target_phase == PHASE_DEVELOPMENT and state.phase != PHASE_DEVELOPMENT_ANALYSIS:
        updates["development_budget_remaining"] = max(0, state.development_budget_remaining - 1)
    elif target_phase == PHASE_REVIEW:
        updates["review_budget_remaining"] = max(0, state.review_budget_remaining - 1)

    if policy is not None:
        updates["current_drain"] = resolve_phase_drain(target_phase, policy)

    return state.copy_with(**updates)


def apply_analysis_success(state: PipelineState, advanced_state: PipelineState) -> PipelineState:
    """Reset inner-loop progress when analysis exits successfully to commit/approval."""
    if state.phase == PHASE_DEVELOPMENT_ANALYSIS:
        return advanced_state.copy_with(development_analysis_iteration=0)
    if state.phase == REVIEW_ANALYSIS_PHASE:
        return advanced_state.copy_with(
            review_issues_found=False,
            review_analysis_iteration=0,
        )
    return advanced_state


def apply_development_analysis_loopback(
    state: PipelineState,
    advanced_state: PipelineState,
) -> PipelineState:
    """Record a development analysis loopback, including capped correction routing."""
    return advanced_state.copy_with(
        development_analysis_iteration=state.development_analysis_iteration + 1
    )


def apply_review_analysis_loopback(
    state: PipelineState,
    advanced_state: PipelineState,
) -> PipelineState:
    """Record a review analysis loopback, including capped correction routing."""
    return advanced_state.copy_with(
        review_issues_found=True,
        review_analysis_iteration=state.review_analysis_iteration + 1,
    )


def apply_commit_outcome(
    state: PipelineState,
    advanced_state: PipelineState,
    *,
    skipped: bool,
) -> PipelineState:
    """Apply canonical outer-progress semantics for commit success vs skip."""
    if state.phase == DEVELOPMENT_COMMIT_PHASE:
        updates: dict[str, object] = {"development_analysis_iteration": 0}
        if not skipped:
            updates["iteration"] = state.iteration + 1
        return advanced_state.copy_with(**updates)

    if state.phase == REVIEW_COMMIT_PHASE:
        updates = {"review_analysis_iteration": 0}
        if not skipped:
            updates["reviewer_pass"] = state.reviewer_pass + 1
        return advanced_state.copy_with(**updates)

    return advanced_state


def derive_run_context_progress(state: PipelineState, run_context: RunContext) -> RunContext:
    """Derive checkpoint-facing progress mirrors from canonical pipeline state."""
    return replace(
        run_context,
        actual_developer_runs=state.iteration,
        actual_reviewer_runs=state.reviewer_pass,
    )
