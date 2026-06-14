"""The analysis cap has exactly one source of truth: live policy.

Regression for the cap-drift bug. Previously the cap was frozen into
``PipelineState.loop_caps`` at run-start and persisted in the checkpoint, then
preferred over live policy. A run started when the default was 3 kept running
analysis 3 times forever, ignoring a later policy change to 1. The fix removes
the per-state snapshot entirely so a policy change always takes effect, including
on resumed runs.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.progress import resolve_analysis_cap
from ralph.pipeline.state import PipelineState
from ralph.policy.models import LoopCounterConfig, PhaseDefinition, PhaseTransition, PipelinePolicy


def _policy_with_cap(cap: int) -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning_analysis": PhaseDefinition(
                drain="analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="planning_analysis",
                    on_failure=None,
                    on_loopback="planning_analysis",
                ),
            )
        },
        loop_counters={"planning_analysis_iteration": LoopCounterConfig(default_max=cap)},
        entry_phase="planning_analysis",
    )


def test_cap_resolves_from_live_policy() -> None:
    assert resolve_analysis_cap("planning_analysis_iteration", _policy_with_cap(1)) == 1


def test_changing_policy_default_changes_resolved_cap() -> None:
    """A later policy default wins — no frozen snapshot can shadow it."""
    field = "planning_analysis_iteration"
    assert resolve_analysis_cap(field, _policy_with_cap(3)) == 3
    assert resolve_analysis_cap(field, _policy_with_cap(1)) == 1


def test_undeclared_counter_is_rejected() -> None:
    with pytest.raises(ValueError, match="not declared"):
        resolve_analysis_cap("missing_iteration", _policy_with_cap(1))


def test_pipeline_state_has_no_loop_caps_field() -> None:
    """loop_caps is removed: there is no second place a cap can live."""
    state = PipelineState(phase="planning_analysis")
    assert not hasattr(state, "loop_caps")
    assert "loop_caps" not in PipelineState.model_fields


def test_resumed_stale_checkpoint_honors_live_policy_cap() -> None:
    """A pre-fix checkpoint that still carries a frozen loop_caps=3 must not
    shadow the live policy. The stale key is dropped on load, and the cap
    resolves from current policy (1) — the exact resume-drift regression."""
    stale_checkpoint_json = (
        '{"phase": "planning_analysis", "loop_caps": {"planning_analysis_iteration": 3}}'
    )
    state = PipelineState.model_validate_json(stale_checkpoint_json)
    assert not hasattr(state, "loop_caps")
    assert resolve_analysis_cap("planning_analysis_iteration", _policy_with_cap(1)) == 1
