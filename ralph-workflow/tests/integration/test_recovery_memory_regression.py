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


_RECOVERY_CYCLE_CAP = 3
_SAMPLE_CYCLES = 5
_CURRENT_BUDGET_BYTES = 512_000
_CHECKPOINT_SIZE_BUDGET_BYTES = 256
_PEAK_BUDGET_BYTES = 5 * 1024 * 1024


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


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


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


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


def _reset_for_next_cycle(state: PipelineState) -> PipelineState:
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

    current_samples: list[int] = []
    checkpoint_sizes: list[int] = []
    history_lengths: list[int] = []
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        for _ in range(_SAMPLE_CYCLES):
            controller = _make_controller()
            state, _, _ = controller.handle(
                state,
                _AgentInactivityTimeoutError("claude idle"),
                phase="development",
                agent="claude",
            )
            assert state.phase == "development"
            assert state.chain_for_phase("development").current_index == 1

            state, _, _ = controller.handle(
                state,
                _AgentInactivityTimeoutError("opencode idle"),
                phase="development",
                agent="opencode",
            )
            assert state.phase == "failed_terminal"
            assert state.recovery_cycle_count >= 1

            ckpt.save(state, checkpoint_path)
            loaded = ckpt.load(checkpoint_path)
            assert loaded is not None
            assert loaded.phase == state.phase
            assert loaded.recovery_cycle_count == state.recovery_cycle_count
            assert len(loaded.fallover_history) == min(
                state.recovery_cycle_count,
                _RECOVERY_CYCLE_CAP,
            )

            current, _peak = tracemalloc.get_traced_memory()
            current_samples.append(current)
            checkpoint_sizes.append(checkpoint_path.stat().st_size)
            history_lengths.append(len(loaded.fallover_history))

            state = _reset_for_next_cycle(loaded)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert history_lengths[:_RECOVERY_CYCLE_CAP] == [1, 2, 3]
    assert history_lengths[-2:] == [3, 3]
    assert max(current_samples) - min(current_samples) < _CURRENT_BUDGET_BYTES
    assert max(checkpoint_sizes[_RECOVERY_CYCLE_CAP - 1 :]) - min(
        checkpoint_sizes[_RECOVERY_CYCLE_CAP - 1 :]
    ) < _CHECKPOINT_SIZE_BUDGET_BYTES
    assert state.recovery_cycle_count == _SAMPLE_CYCLES
    assert len(state.fallover_history) == _RECOVERY_CYCLE_CAP
    assert peak < _PEAK_BUDGET_BYTES

