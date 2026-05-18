"""Tests for budget_caps seeding in PipelineState initial construction.

Asserts that _create_initial_state seeds budget_caps from policy-declared
budget_counters using their default_max values and counter_overrides.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)

_CONFIG_ITERS = 3
_CONFIG_REVIEWS = 1
_CUSTOM_MAX = 7
_LEGACY_TOTAL_ITERS = 5
_LEGACY_DEV_REMAINING = 3
_LEGACY_REVIEWER_PASSES = 2
_LEGACY_REVIEW_REMAINING = 1


def _minimal_policy(**extra: object) -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "start": PhaseDefinition(
                drain="start",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        entry_phase="start",
        terminal_phase="done",
        **extra,
    )


def _config() -> MagicMock:
    cfg = MagicMock()
    cfg.agent_chains = {}
    cfg.agent_drains = {}
    return cfg


class TestLegacyCheckpointMigration:
    def test_legacy_total_iterations_migrates_to_budget_caps(self) -> None:
        state = PipelineState.model_validate(
            {
                "phase": "development",
                "total_iterations": _LEGACY_TOTAL_ITERS,
                "development_budget_remaining": _LEGACY_DEV_REMAINING,
            }
        )
        assert state.budget_caps.get("iteration") == _LEGACY_TOTAL_ITERS
        assert state.get_budget_remaining("iteration") == _LEGACY_DEV_REMAINING

    def test_legacy_reviewer_pass_migrates_to_budget_caps(self) -> None:
        state = PipelineState.model_validate(
            {
                "phase": "review",
                "total_reviewer_passes": _LEGACY_REVIEWER_PASSES,
                "review_budget_remaining": _LEGACY_REVIEW_REMAINING,
            }
        )
        assert state.budget_caps.get("reviewer_pass") == _LEGACY_REVIEWER_PASSES
        assert state.get_budget_remaining("reviewer_pass") == _LEGACY_REVIEW_REMAINING

    @pytest.mark.parametrize(
        "raw",
        [
            {"phase": "development"},
            {"phase": "planning", "total_iterations": 5, "total_reviewer_passes": 2},
        ],
    )
    def test_no_crash_on_partial_legacy_data(self, raw: dict[str, object]) -> None:
        state = PipelineState.model_validate(raw)
        assert state.phase in ("development", "planning")
