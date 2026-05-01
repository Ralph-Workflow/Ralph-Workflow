"""Tests for budget_caps seeding in PipelineState initial construction.

Asserts that _create_initial_state seeds budget_caps from policy-declared
budget_counters, with config.general overrides for the canonical counters.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    BudgetCounterConfig,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)

_CONFIG_ITERS = 3
_CONFIG_REVIEWS = 1
_CUSTOM_MAX = 7
_FALLBACK_DEFAULT = 5


def _minimal_policy(**extra: Any) -> PipelinePolicy:
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


def _config(developer_iters: int = 5, reviewer_reviews: int = 2) -> MagicMock:
    cfg = MagicMock()
    cfg.general.developer_iters = developer_iters
    cfg.general.reviewer_reviews = reviewer_reviews
    cfg.agent_chains = {}
    cfg.agent_drains = {}
    return cfg


class TestBudgetCapsSeeding:
    def test_policy_counters_seeded_from_config_general(self) -> None:
        policy = _minimal_policy(
            budget_counters={
                "iteration": BudgetCounterConfig(),
                "reviewer_pass": BudgetCounterConfig(),
            }
        )
        state = runner_module._create_initial_state(
            _config(_CONFIG_ITERS, _CONFIG_REVIEWS), pipeline_policy=policy
        )
        assert state.budget_caps["iteration"] == _CONFIG_ITERS
        assert state.budget_caps["reviewer_pass"] == _CONFIG_REVIEWS

    def test_custom_counter_uses_policy_default_max(self) -> None:
        policy = _minimal_policy(
            budget_counters={"attempts": BudgetCounterConfig(default_max=_CUSTOM_MAX)}
        )
        state = runner_module._create_initial_state(_config(), pipeline_policy=policy)
        assert state.budget_caps["attempts"] == _CUSTOM_MAX

    def test_custom_counter_without_default_max_falls_back_to_five(self) -> None:
        policy = _minimal_policy(
            budget_counters={"attempts": BudgetCounterConfig()}
        )
        state = runner_module._create_initial_state(_config(), pipeline_policy=policy)
        assert state.budget_caps["attempts"] == _FALLBACK_DEFAULT

    def test_budget_remaining_mirrors_budget_caps_at_startup(self) -> None:
        policy = _minimal_policy(
            budget_counters={
                "iteration": BudgetCounterConfig(),
                "attempts": BudgetCounterConfig(default_max=4),
            }
        )
        state = runner_module._create_initial_state(_config(6), pipeline_policy=policy)
        assert state.get_budget_remaining("iteration") == state.budget_caps["iteration"]
        assert state.get_budget_remaining("attempts") == state.budget_caps["attempts"]

    def test_empty_budget_counters_produces_empty_caps(self) -> None:
        policy = _minimal_policy()
        state = runner_module._create_initial_state(_config(), pipeline_policy=policy)
        assert state.budget_caps == {}

    def test_iteration_not_seeded_when_not_in_policy(self) -> None:
        policy = _minimal_policy(
            budget_counters={"reviewer_pass": BudgetCounterConfig()}
        )
        state = runner_module._create_initial_state(
            _config(10, _CONFIG_REVIEWS), pipeline_policy=policy
        )
        assert "iteration" not in state.budget_caps
        assert state.budget_caps["reviewer_pass"] == _CONFIG_REVIEWS

    def test_zero_reviewer_reviews_caps_at_zero(self) -> None:
        policy = _minimal_policy(
            budget_counters={"reviewer_pass": BudgetCounterConfig()}
        )
        state = runner_module._create_initial_state(
            _config(reviewer_reviews=0), pipeline_policy=policy
        )
        assert state.budget_caps["reviewer_pass"] == 0
        assert state.get_budget_remaining("reviewer_pass") == 0


_LEGACY_TOTAL_ITERS = 3
_LEGACY_DEV_REMAINING = 2
_LEGACY_REVIEWER_PASSES = 2
_LEGACY_REVIEW_REMAINING = 1


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
    def test_no_crash_on_partial_legacy_data(self, raw: dict[str, Any]) -> None:
        state = PipelineState.model_validate(raw)
        assert state.phase in ("development", "planning")
