"""Unit tests for the run pipeline CLI command."""

from __future__ import annotations

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.validation import validate_cli_counter_overrides

_EXIT_PREFLIGHT = 2


def _policy_bundle_for_testing() -> PolicyBundle:
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
                "complete": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
                "complete": AgentDrainConfig(chain="complete"),
            },
        ),
        pipeline=PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )


class TestValidateCounterOverrides:
    """Tests for CLI counter override validation via the shared policy validator."""

    def _pipeline_with_counters(self, *counter_names: str) -> PipelinePolicy:

        return PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    transitions=PhaseTransition(on_success="work"),
                )
            },
            entry_phase="work",
            terminal_phase="work",
            budget_counters={name: BudgetCounterConfig(default_max=5) for name in counter_names},
        )

    def test_unknown_counter_raises_policy_validation_error(self) -> None:

        policy = self._pipeline_with_counters("declared_counter")
        errors: list[str] = []
        validate_cli_counter_overrides(policy, {"unknown_counter": 3}, errors)
        assert any("unknown_counter" in e for e in errors)

    def test_declared_counter_passes_validation(self) -> None:

        policy = self._pipeline_with_counters("my_counter")
        errors: list[str] = []
        validate_cli_counter_overrides(policy, {"my_counter": 5}, errors)
        assert errors == []

    def test_empty_overrides_passes_validation(self) -> None:

        policy = self._pipeline_with_counters()
        errors: list[str] = []
        validate_cli_counter_overrides(policy, {}, errors)
        assert errors == []
