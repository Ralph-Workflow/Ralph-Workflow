"""Regression harness for recovery-lineage memory retention and checkpoint growth."""

from __future__ import annotations

import gc
import tempfile
import tracemalloc
from pathlib import Path

import pytest

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController

_RECOVERY_CYCLE_CAP = 4
_RECOVERY_ITERATION_COUNT = 20
_RETAINED_DELTA_SPREAD_LIMIT = 2_000_000
_PEAK_DELTA_LIMIT = 20_000_000
_CHECKPOINT_SIZE_SPREAD_LIMIT = 2_048


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"

def _minimal_policy_bundle():
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_state() -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"],
                current_index=0,
                retries=0,
            )
        },
        recovery_cycle_cap=_RECOVERY_CYCLE_CAP,
    )


def _make_controller() -> RecoveryController:
    registry = (
        AgentBudgetRegistry()
        .set_budget("development", "claude", max_retries=1)
        .set_budget("development", "opencode", max_retries=1)
    )
    return RecoveryController(
        cycle_cap=_RECOVERY_CYCLE_CAP,
        budget_registry=registry,
        policy_bundle=_minimal_policy_bundle(),
    )


def _resume_next_cycle(state: PipelineState) -> PipelineState:
    return state.copy_with(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"],
                current_index=0,
                retries=0,
            )
        },
    )


@pytest.mark.integration
def test_recovery_memory_regression(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    state = _make_state()
    retained_deltas: list[int] = []
    checkpoint_sizes: list[int] = []

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    for cycle in range(1, _RECOVERY_ITERATION_COUNT + 1):
        controller = _make_controller()
        state, _, _ = controller.handle(
            state,
            _AgentInactivityTimeoutError("claude idle"),
            phase="development",
            agent="claude",
        )
        state, _, _ = controller.handle(
            state,
            _AgentInactivityTimeoutError("opencode idle"),
            phase="development",
            agent="opencode",
        )

        assert state.phase == "failed_terminal"
        assert state.recovery_cycle_count == cycle

        ckpt.save(state, checkpoint_path)
        loaded = ckpt.load(checkpoint_path)

        assert loaded is not None

        current_current, _ = tracemalloc.get_traced_memory()
        retained_deltas.append(current_current - baseline_current)
        checkpoint_sizes.append(checkpoint_path.stat().st_size)

        if cycle >= _RECOVERY_CYCLE_CAP:
            assert len(state.fallover_history) == _RECOVERY_CYCLE_CAP
            assert len(loaded.fallover_history) == _RECOVERY_CYCLE_CAP

        state = _resume_next_cycle(loaded)

    gc.collect()
    final_current, peak_current = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    post_warmup_deltas = retained_deltas[_RECOVERY_CYCLE_CAP - 1 :]
    post_warmup_sizes = checkpoint_sizes[_RECOVERY_CYCLE_CAP - 1 :]

    assert post_warmup_deltas
    assert post_warmup_sizes
    assert max(post_warmup_deltas) - min(post_warmup_deltas) <= _RETAINED_DELTA_SPREAD_LIMIT
    assert peak_current - baseline_current <= _PEAK_DELTA_LIMIT
    assert max(post_warmup_sizes) - min(post_warmup_sizes) <= _CHECKPOINT_SIZE_SPREAD_LIMIT
    assert final_current - baseline_current <= _PEAK_DELTA_LIMIT
