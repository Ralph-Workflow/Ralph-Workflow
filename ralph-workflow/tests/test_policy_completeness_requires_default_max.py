"""Contract test: BudgetCounterConfig.default_max must be required.

The silent fallback to _DEFAULT_BUDGET_CAP=5 is being removed.
BudgetCounterConfig must require an explicit default_max so the runtime
never invents a hidden cap. Additionally, tracked budget counters with
default_max=0 are rejected at policy completeness validation because a
zero budget means the pipeline can never start.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
)
from ralph.policy.validation import PolicyValidationError, validate_policy_completeness


def _bundle_with_tracked_zero_counter(counter_name: str) -> PolicyBundle:
    """Build a minimal policy bundle where a tracked counter has default_max=0."""
    pipeline = PipelinePolicy(
        entry_phase="work",
        terminal_phase="done",
        budget_counters={
            counter_name: BudgetCounterConfig(
                tracks_budget=True,
                description="zero-budget counter",
                default_max=0,
            )
        },
        phases={
            "work": PhaseDefinition(
                drain="work",
                role="commit",
                transitions=PhaseTransition(on_success="done"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter=counter_name,
                    loop_resets=[],
                ),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="work", budget_state="remaining"),
                target="done",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="work", budget_state="exhausted"),
                target="done",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="work", budget_state="no_review"),
                target="done",
            ),
        ],
    )
    agents = AgentsPolicy(
        agent_chains={"c": AgentChainConfig(agents=["fake-agent"])},
        agent_drains={
            "work": AgentDrainConfig(chain="c"),
            "done": AgentDrainConfig(chain="c"),
        },
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())


def test_budget_counter_config_requires_default_max() -> None:
    """Constructing BudgetCounterConfig without default_max must raise ValidationError."""
    with pytest.raises((ValidationError, TypeError)):
        BudgetCounterConfig(tracks_budget=True, description="test counter")


def test_budget_counter_config_with_explicit_default_max_succeeds() -> None:
    """Constructing BudgetCounterConfig with an explicit default_max succeeds."""
    cfg = BudgetCounterConfig(tracks_budget=True, description="test", default_max=5)
    assert cfg.default_max == 5  # noqa: PLR2004


def test_budget_counter_config_zero_default_max_is_valid_for_untracked() -> None:
    """A default_max of 0 is accepted for non-tracking counters."""
    cfg = BudgetCounterConfig(tracks_budget=False, description="untracked", default_max=0)
    assert cfg.default_max == 0


def test_validate_policy_completeness_rejects_tracked_zero_budget_counter() -> None:
    """validate_policy_completeness rejects tracks_budget=True with default_max=0."""
    bundle = _bundle_with_tracked_zero_counter("my_counter")
    with pytest.raises(PolicyValidationError) as exc_info:
        validate_policy_completeness(bundle)
    msg = str(exc_info.value)
    assert "my_counter" in msg
    assert "default_max=0" in msg or "tracks_budget=True" in msg


def test_validate_policy_completeness_rejects_custom_named_zero_tracked_counter() -> None:
    """The tracked-zero check applies to any counter name, not just canonical names."""
    bundle = _bundle_with_tracked_zero_counter("cycles")
    with pytest.raises(PolicyValidationError) as exc_info:
        validate_policy_completeness(bundle)
    assert "cycles" in str(exc_info.value)


def test_validate_policy_completeness_accepts_untracked_zero_counter() -> None:
    """A zero default_max is allowed when tracks_budget=False."""
    from ralph.policy.models import RecoveryPolicy

    pipeline = PipelinePolicy(
        entry_phase="work",
        terminal_phase="done",
        budget_counters={
            "audit": BudgetCounterConfig(
                tracks_budget=False,
                description="untracked counter",
                default_max=0,
            )
        },
        phases={
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="done", on_failure="failed_terminal"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    agents = AgentsPolicy(
        agent_chains={"c": AgentChainConfig(agents=["fake-agent"])},
        agent_drains={
            "work": AgentDrainConfig(chain="c"),
            "done": AgentDrainConfig(chain="c"),
        },
    )
    bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
    # Should not raise
    validate_policy_completeness(bundle)
