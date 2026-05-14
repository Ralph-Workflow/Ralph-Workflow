"""Regression harness for recovery-lineage memory retention and checkpoint growth."""

from __future__ import annotations

import tracemalloc
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


_RECOVERY_CYCLE_CAP = 4
_RECOVERY_ITERATION_COUNT = 8
_RETAINED_DELTA_SPREAD_LIMIT = 2_000_000
_PEAK_DELTA_LIMIT = 20_000_000
_CHECKPOINT_SIZE_SPREAD_LIMIT = 2_048


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _make_state(
    *,
    fallover_history: tuple = (),
    recovery_cycle_count: int = 0,
) -> PipelineState:
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
        fallover_history=fallover_history,
        recovery_cycle_count=recovery_cycle_count,
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
        policy_bundle=_load_default_policy_bundle(),
    )


def _resume_next_cycle(state: PipelineState) -> PipelineState:
    return state.copy_with(
        phase="development",
        previous_phase=None,
        last_error=None,
        last_agent_session_id=None,
        session_preserve_retry_pending=False,
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"],
                current_index=0,
                retries=0,
            )
        },
    )


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_recovery_memory_regression(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    state = _make_state()
    retained_deltas: list[int] = []
    checkpoint_sizes: list[int] = []

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
        assert state.phase == "development"
        development_chain = state.chain_for_phase("development")
        assert development_chain is not None
        assert development_chain.current_index == 1

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
        assert loaded.phase == state.phase
        assert loaded.recovery_cycle_count == state.recovery_cycle_count

        current_current, _ = tracemalloc.get_traced_memory()
        retained_deltas.append(current_current - baseline_current)
        checkpoint_sizes.append(checkpoint_path.stat().st_size)

        if cycle >= _RECOVERY_CYCLE_CAP:
            assert len(state.fallover_history) == _RECOVERY_CYCLE_CAP
            assert len(loaded.fallover_history) == _RECOVERY_CYCLE_CAP

        state = _resume_next_cycle(loaded)

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
