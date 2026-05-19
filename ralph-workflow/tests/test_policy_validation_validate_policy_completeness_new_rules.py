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


class TestValidatePolicyCompletenessNewRules:
    """Tests for vocab superset check, commit_policy, loop_resets, and failed_route."""

    def _minimal_agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = cast(
            "dict[DrainName, AgentDrainConfig]",
            {d: AgentDrainConfig(chain=d) for d in drains},
        )
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _minimal_analysis_phase(
        self,
        name: DrainName,
        iteration_field: str,
        on_success: str = "complete",
        failure_target: str = "failed",
    ) -> PhaseDefinition:
        """Create a minimal analysis phase with required decisions field."""
        return PhaseDefinition(
            drain=name,
            role="analysis",
            transitions=PhaseTransition(
                on_success=on_success,
                on_loopback=name,
            ),
            loop_policy=PhaseLoopPolicy(iteration_state_field=iteration_field),
            decisions={
                "completed": PhaseDecisionRoute(target=on_success, reset_loop=True),
                "failed": PhaseDecisionRoute(target=failure_target, reset_loop=False),
            },
        )

    def _minimal_analysis_artifacts(self, drain: DrainName) -> ArtifactsPolicy:
        """Create minimal artifacts policy for analysis phase."""
        return ArtifactsPolicy(
            artifacts={
                "dev_analysis": ArtifactContract(
                    drain=drain,
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "failed"],
                )
            }
        )

    def test_uncovered_vocab_entry_raises(self) -> None:
        """Analysis phase decisions must cover every entry in decision_vocabulary."""
        agents = self._minimal_agents(["development_analysis", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis", "development_analysis_iteration"
                ),
                "failed": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed", on_loopback="failed"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(
            artifacts={
                "dev_analysis": ArtifactContract(
                    drain="development_analysis",
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "rejected"],
                )
            }
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="vocab entry 'rejected' has no route"):
            validate_policy_completeness(bundle)

    def test_uncovered_vocab_with_on_failure_still_raises(self) -> None:
        """Uncovered vocab entries fail even when transitions.on_failure is set.

        The on_failure escape hatch was removed - every decision_vocabulary entry
        must have an explicit route in the decisions table.
        """
        agents = self._minimal_agents(["development_analysis", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions={
                        "completed": PhaseDecisionRoute(target="complete", reset_loop=True),
                    },
                ),
                "failed": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed", on_loopback="failed"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(
            artifacts={
                "dev_analysis": ArtifactContract(
                    drain="development_analysis",
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "rejected"],
                )
            }
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="vocab entry 'rejected' has no route"):
            validate_policy_completeness(bundle)

    def test_commit_phase_without_commit_policy_raises(self) -> None:
        """A commit-role phase with commit_policy=None fails completeness check."""
        agents = self._minimal_agents(["development_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    # commit_policy intentionally absent
                ),
                "failed": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed", on_loopback="failed"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_commit",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="role='commit' requires commit_policy"):
            validate_policy_completeness(bundle)

    def test_commit_phase_with_commit_policy_passes(self) -> None:
        """A commit-role phase with commit_policy passes completeness check."""
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis",
                    "development_analysis_iteration",
                    on_success="development_commit",
                    failure_target="failed_terminal",
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)  # must not raise

    def test_commit_phase_with_increments_counter_none_is_valid(self) -> None:
        """A commit-role phase with increments_counter='none' passes completeness check.

        increments_counter='none' is a valid declared value — it indicates that
        this commit phase does not advance outer progress (e.g., a verification-style
        commit that just validates without bumping iteration or reviewer_pass).
        """
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis",
                    "development_analysis_iteration",
                    on_success="development_commit",
                    failure_target="failed_terminal",
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="none",  # Valid — no outer-progress bump
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)  # must not raise

    def test_commit_policy_loop_resets_invalid_field_raises(self) -> None:
        """commit_policy.loop_resets with an invalid iteration field fails validation."""
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        dev_analysis_decisions = {
            "completed": PhaseDecisionRoute(target="development_commit", reset_loop=True),
            "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
        }
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions=dev_analysis_decisions,
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        # "nonexistent_iteration_field" is not an iteration_state_field
                        # used by any analysis phase in this policy
                        loop_resets=["nonexistent_iteration_field"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="invalid iteration field"):
            validate_policy_completeness(bundle)

    def test_commit_policy_loop_resets_valid_field_passes(self) -> None:
        """commit_policy.loop_resets referencing a valid iteration field passes validation."""
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        dev_analysis_decisions = {
            "completed": PhaseDecisionRoute(target="development_commit", reset_loop=True),
            "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
        }
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions=dev_analysis_decisions,
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        # References the iteration_state_field from development_analysis
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)  # must not raise

    def test_recovery_terminal_recovery_route_field_rejected(self) -> None:
        """terminal_recovery_route is deprecated; the model validator rejects it."""
        with pytest.raises(ValidationError, match="deprecated"):
            RecoveryPolicy.model_validate(
                {
                    "cycle_cap": 200,
                    "terminal_recovery_route": "some_phase",
                    "preserve_session_on_categories": ("agent",),
                }
            )

    def test_recovery_failed_route_unknown_phase_raises_policy_error(self) -> None:
        """failed_route referencing an undeclared phase fails completeness validation.

        A non-reserved string that doesn't match a declared phase is rejected by
        validate_policy_completeness, not at Pydantic model level.
        Note: 'phase_failed' and 'exit_failure' are rejected at model construction.
        """
        agents = self._minimal_agents(["planning", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(
                cycle_cap=200,
                failed_route="nonexistent_phase",
                preserve_session_on_categories=("agent",),
            ),
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="nonexistent_phase"):
            validate_policy_completeness(bundle)

    def test_recovery_failed_route_declared_phase_accepted(self) -> None:
        """failed_route set to a declared pipeline phase is valid."""
        agents = self._minimal_agents(["planning", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(
                cycle_cap=200,
                failed_route="planning",
                preserve_session_on_categories=("agent",),
            ),
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)  # must not raise

    def test_recovery_failed_route_phase_failed_is_rejected(self) -> None:
        """recovery.failed_route='phase_failed' is rejected at model construction."""
        with pytest.raises(ValidationError, match="no longer supported"):
            RecoveryPolicy(
                cycle_cap=200,
                failed_route="phase_failed",
                preserve_session_on_categories=("agent",),
            )

    def test_recovery_failed_route_exit_failure_is_rejected(self) -> None:
        """recovery.failed_route='exit_failure' is rejected at model construction."""
        with pytest.raises(ValidationError, match="no longer supported"):
            RecoveryPolicy(
                cycle_cap=200,
                failed_route="exit_failure",
                preserve_session_on_categories=("agent",),
            )

    def test_review_role_requires_issues_outcome(self) -> None:
        """role='review' without issues_outcome fails validate_policy_completeness."""
        agents = self._minimal_agents(["planning", "review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    transitions=PhaseTransition(on_success="complete"),
                    # issues_outcome intentionally omitted
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="issues_outcome"):
            validate_policy_completeness(bundle)

    def test_review_role_requires_clean_outcome_when_bypass_routes_set(self) -> None:
        """role='review' with bypass_routes but no clean_outcome fails completeness check."""
        agents = self._minimal_agents(["planning", "review", "review_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={"review_clean": "review_commit"},
                    # clean_outcome intentionally omitted while bypass_routes is set
                ),
                "review_commit": PhaseDefinition(
                    drain="review_commit",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="clean_outcome"):
            validate_policy_completeness(bundle)
