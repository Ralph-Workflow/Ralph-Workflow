"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

import importlib
from typing import cast

import pytest

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    DrainName,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_policy_completeness,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidatePolicyCompletenessReachability:
    """Tests for phase reachability validation in validate_policy_completeness.

    Every phase declared in policy.phases must be reachable from entry_phase
    following any combination of transitions (on_success, on_failure, on_loopback,
    decisions, bypass_routes). Orphaned phases that can never be reached from the
    entry point are rejected as incomplete policy.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = cast(
            "dict[DrainName, AgentDrainConfig]",
            {d: AgentDrainConfig(chain=d) for d in drains},
        )
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_linear_chain_all_reachable_passes(self) -> None:
        """Simple entry -> middle -> terminal: all phases reachable, validation passes."""
        agents = self._agents(["planning", "development", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_orphaned_phase_raises_validation_error(self) -> None:
        """A phase defined in policy but unreachable from entry_phase fails validation."""
        agents = self._agents(["planning", "orphan", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "orphan": PhaseDefinition(
                    drain="orphan",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError, match="orphan"):
            validate_policy_completeness(bundle)

    def test_phase_reachable_via_on_failure_passes(self) -> None:
        """A phase reachable only via on_failure is still considered reachable."""
        agents = self._agents(["planning", "fallback", "complete", "crashed"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="fallback",
                    ),
                ),
                "fallback": PhaseDefinition(
                    drain="fallback",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="crashed",
                    ),
                ),
                "complete": self._terminal_phase(),
                "crashed": PhaseDefinition(
                    drain="crashed",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_phase_reachable_via_on_loopback_passes(self) -> None:
        """A phase reachable only via on_loopback is still considered reachable."""
        agents = self._agents(["execution", "review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "execution": PhaseDefinition(
                    drain="execution",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="review",
                        on_loopback="execution",
                    ),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="execution",
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="execution",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_phase_reachable_via_decision_target_passes(self) -> None:
        """A phase reachable only via an analysis decisions target is reachable."""
        agents = self._agents(["analysis", "alt_path", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "analysis": PhaseDefinition(
                    drain="analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions={
                        "completed": PhaseDecisionRoute(target="complete", reset_loop=True),
                        "needs_work": PhaseDecisionRoute(target="alt_path", reset_loop=False),
                    },
                ),
                "alt_path": PhaseDefinition(
                    drain="alt_path",
                    role="execution",
                    transitions=PhaseTransition(on_success="analysis"),
                ),
                "complete": self._terminal_phase(),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="analysis",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        artifacts = ArtifactsPolicy(
            artifacts={
                "art": ArtifactContract(
                    drain="analysis",
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "needs_work"],
                )
            }
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)  # must not raise

    def test_phase_reachable_via_bypass_route_passes(self) -> None:
        """A phase reachable only via bypass_routes is still considered reachable."""
        agents = self._agents(["review", "shortcut_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="review_clean",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="review",
                    ),
                    bypass_routes={"review_clean": "shortcut_commit"},
                ),
                "shortcut_commit": PhaseDefinition(
                    drain="shortcut_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="none",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_multiple_orphaned_phases_all_listed_in_error(self) -> None:
        """All unreachable phases must appear in the validation error message."""
        agents = self._agents(["planning", "orphan_a", "orphan_b", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "orphan_a": PhaseDefinition(
                    drain="orphan_a",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "orphan_b": PhaseDefinition(
                    drain="orphan_b",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        error_msg = str(exc_info.value)
        assert "orphan_a" in error_msg
        assert "orphan_b" in error_msg
