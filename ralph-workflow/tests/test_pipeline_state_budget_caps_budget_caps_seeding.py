"""Tests for budget_caps seeding in PipelineState initial construction.

Asserts that _create_initial_state seeds budget_caps from policy-declared
budget_counters using their default_max values and counter_overrides.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from ralph.pipeline import runner as runner_module
from ralph.policy.models import (
    BudgetCounterConfig,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)

_CONFIG_ITERS = 3
_CONFIG_REVIEWS = 1
_CUSTOM_MAX = 7


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


class TestBudgetCapsSeeding:
    def test_counter_overrides_set_initial_caps(self) -> None:
        policy = _minimal_policy(
            budget_counters={
                "iteration": BudgetCounterConfig(default_max=5),
                "reviewer_pass": BudgetCounterConfig(default_max=1),
            }
        )
        state = runner_module.create_initial_state(
            _config(),
            pipeline_policy=policy,
            counter_overrides={"iteration": _CONFIG_ITERS, "reviewer_pass": _CONFIG_REVIEWS},
        )
        assert state.budget_caps["iteration"] == _CONFIG_ITERS
        assert state.budget_caps["reviewer_pass"] == _CONFIG_REVIEWS

    def test_custom_counter_uses_policy_default_max(self) -> None:
        policy = _minimal_policy(
            budget_counters={"attempts": BudgetCounterConfig(default_max=_CUSTOM_MAX)}
        )
        state = runner_module.create_initial_state(_config(), pipeline_policy=policy)
        assert state.budget_caps["attempts"] == _CUSTOM_MAX

    def test_budget_counter_config_requires_explicit_default_max(self) -> None:
        """BudgetCounterConfig must be constructed with explicit default_max."""
        with pytest.raises((ValidationError, TypeError)):
            BudgetCounterConfig()

    def test_budget_remaining_mirrors_budget_caps_at_startup(self) -> None:
        policy = _minimal_policy(
            budget_counters={
                "iteration": BudgetCounterConfig(default_max=6),
                "attempts": BudgetCounterConfig(default_max=4),
            }
        )
        state = runner_module.create_initial_state(
            _config(), pipeline_policy=policy, counter_overrides={"iteration": 6}
        )
        assert state.get_budget_remaining("iteration") == state.budget_caps["iteration"]
        assert state.get_budget_remaining("attempts") == state.budget_caps["attempts"]

    def test_empty_budget_counters_produces_empty_caps(self) -> None:
        policy = _minimal_policy()
        state = runner_module.create_initial_state(_config(), pipeline_policy=policy)
        assert state.budget_caps == {}

    def test_iteration_not_seeded_when_not_in_policy(self) -> None:
        policy = _minimal_policy(
            budget_counters={"reviewer_pass": BudgetCounterConfig(default_max=1)}
        )
        state = runner_module.create_initial_state(
            _config(),
            pipeline_policy=policy,
            counter_overrides={"reviewer_pass": _CONFIG_REVIEWS},
        )
        assert "iteration" not in state.budget_caps
        assert state.budget_caps["reviewer_pass"] == _CONFIG_REVIEWS

    def test_zero_reviewer_pass_counter_override(self) -> None:
        policy = _minimal_policy(
            budget_counters={"reviewer_pass": BudgetCounterConfig(default_max=1)}
        )
        state = runner_module.create_initial_state(
            _config(), pipeline_policy=policy, counter_overrides={"reviewer_pass": 0}
        )
        assert state.budget_caps["reviewer_pass"] == 0
        assert state.get_budget_remaining("reviewer_pass") == 0
