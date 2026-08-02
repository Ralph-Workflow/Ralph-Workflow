"""Consolidated policy validation tests.

This module merges the following previously split test modules into a single
file to reduce per-shard collection cost. The original class names are
preserved so external references (test::TestX) still resolve.

Source files (all test_policy_validation_*.py under tests/):
  - test_policy_validation_advance_phase_requires_policy_for_commit_targets.py
  - test_policy_validation_agents_policy_validation.py
  - test_policy_validation_apply_commit_outcome_requires_policy.py
  - test_policy_validation_checkpoint_policy_mismatch_error.py
  - test_policy_validation_default_policy_loading.py
  - test_policy_validation_forbid_sibling_drain_inference.py
  - test_policy_validation_get_drain_resolution_matrix.py
  - test_policy_validation_load_policy_forbid_sibling_inference.py
  - test_policy_validation_pipeline_owned_artifact_required_policy.py
  - test_policy_validation_policy_bundle_validation.py
  - test_policy_validation_shared_drain_history_consistency.py
  - test_policy_validation_strict_cli_counter_overrides.py
  - test_policy_validation_strict_legacy_fields_rejected.py
  - test_policy_validation_strict_parallelization_consistency.py
  - test_policy_validation_strict_skip_invocation_has_on_success.py
  - test_policy_validation_validate_chain_exists.py
  - test_policy_validation_validate_checkpoint_compatible.py
  - test_policy_validation_validate_drain_bound.py
  - test_policy_validation_validate_phase_exists_in_policy.py
  - test_policy_validation_validate_policy_completeness_new_rules.py
  - test_policy_validation_validate_policy_completeness_reachability.py
  - test_policy_validation_validate_policy_completeness_verification_role.py
  - test_policy_validation_validate_post_commit_all_budget_states_covered.py
  - test_policy_validation_validate_post_commit_routes_coverage.py
  - test_policy_validation_validate_required_inputs.py
  - test_policy_validation_validate_review_phase_outcome_complete.py
  - test_policy_validation_validate_terminal_failure_phase_required.py
  - test_policy_validation_validate_work_units_against_policy.py
"""

from __future__ import annotations

import importlib
from dataclasses import fields
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest

from ralph.cli.commands.init import STARTER_PROMPT_SENTINEL
from ralph.mcp.protocol.capability_mapping import drain_class_for_session
from ralph.phases.required_artifacts import (
    RequiredArtifact,
    resolve_phase_required_artifact,
)
from ralph.pipeline.progress import (
    advance_phase,
    apply_commit_outcome,
)
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import parse_work_units_from_artifact
from ralph.policy.loader import (
    PolicyValidationError as LoaderPolicyValidationError,
)
from ralph.policy.loader import (
    load_policy,
)
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactHistoryPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    DrainName,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseParallelization,
    PhaseTransition,
    PhaseVerificationPolicy,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
    get_drain_resolution_matrix,
    validate_chain_exists,
    validate_checkpoint_compatible,
    validate_drain_bound,
    validate_drain_contracts,
    validate_phase_exists_in_policy,
    validate_policy_completeness,
    validate_required_inputs,
    validate_work_units_against_policy,
)

DEFAULT_MAX_WORK_UNITS = 50

ValidationError = importlib.import_module("pydantic").ValidationError




# === Helper for test_policy_validation_strict_cli_counter_overrides.py ===
def _strict_cli_counter_overrides_minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


# === Helper for test_policy_validation_strict_cli_counter_overrides.py ===
def _strict_cli_counter_overrides_terminal_phase(drain: str = "complete", outcome: str = "success") -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


# === Helper for test_policy_validation_strict_cli_counter_overrides.py ===
def _strict_cli_counter_overrides_minimal_bundle_with_phases(phases: dict[str, PhaseDefinition]) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _strict_cli_counter_overrides_minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


# === Helper for test_policy_validation_strict_legacy_fields_rejected.py ===
def _strict_legacy_fields_rejected_minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


# === Helper for test_policy_validation_strict_legacy_fields_rejected.py ===
def _strict_legacy_fields_rejected_terminal_phase(drain: str = "complete", outcome: str = "success") -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


# === Helper for test_policy_validation_strict_legacy_fields_rejected.py ===
def _strict_legacy_fields_rejected_minimal_bundle_with_phases(phases: dict[str, PhaseDefinition]) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _strict_legacy_fields_rejected_minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


# === Helper for test_policy_validation_strict_parallelization_consistency.py ===
def _strict_parallelization_consist_minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


# === Helper for test_policy_validation_strict_parallelization_consistency.py ===
def _strict_parallelization_consist_terminal_phase(drain: str = "complete", outcome: str = "success") -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


# === Helper for test_policy_validation_strict_parallelization_consistency.py ===
def _strict_parallelization_consist_minimal_bundle_with_phases(phases: dict[str, PhaseDefinition]) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _strict_parallelization_consist_minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


# === Helper for test_policy_validation_strict_skip_invocation_has_on_success.py ===
def _strict_skip_invocation_has_on__minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


# === Helper for test_policy_validation_strict_skip_invocation_has_on_success.py ===
def _strict_skip_invocation_has_on__terminal_phase(drain: str = "complete", outcome: str = "success") -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


# === Helper for test_policy_validation_strict_skip_invocation_has_on_success.py ===
def _strict_skip_invocation_has_on__minimal_bundle_with_phases(phases: dict[str, PhaseDefinition]) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _strict_skip_invocation_has_on__minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


# === consolidated from test_policy_validation_advance_phase_requires_policy_for_commit_targets.py ===
class TestAdvancePhaseRequiresPolicyForCommitTargets:
    """Tests that advance_phase raises when policy is None."""

    def test_raises_value_error_when_policy_is_none(self) -> None:

        state = PipelineState(phase="development_commit")
        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            advance_phase(state, "development", policy=None)


# === consolidated from test_policy_validation_agents_policy_validation.py ===
class TestAgentsPolicyValidation:
    """Tests for AgentsPolicy model validation."""

    def test_valid_agents_policy(self) -> None:
        """Test that a valid agents policy passes validation."""
        policy = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )
        assert policy.agent_chains["planning"].agents == ["claude"]
        assert policy.agent_drains["planning"].chain == "planning"

    def test_drain_references_unknown_chain_raises(self) -> None:
        """Test that a drain binding to an unknown chain raises ValueError."""
        with pytest.raises(ValueError, match="references unknown chain"):
            AgentsPolicy(
                agent_chains={},
                agent_drains={
                    "planning": AgentDrainConfig(chain="nonexistent"),
                },
            )

    def test_empty_chain_list_raises(self) -> None:
        """Test that an empty agents list in a chain raises ValueError."""
        with pytest.raises(ValidationError, match="too_short"):
            AgentChainConfig(agents=[])

    def test_chain_referencing_unknown_agent_raises(self) -> None:
        """Test that chain with unknown agent name raises validation error.

        Note: The model doesn't validate agent names exist, only that the chain
        reference is valid. Agent name validation happens at the registry level.
        """
        # This is valid at the policy level - agent names are validated elsewhere
        chain = AgentChainConfig(agents=["nonexistent_agent"], max_retries=2)
        assert chain.agents == ["nonexistent_agent"]


# === consolidated from test_policy_validation_apply_commit_outcome_requires_policy.py ===
class TestApplyCommitOutcomeRequiresPolicy:
    """Tests that apply_commit_outcome raises when policy is None."""

    def test_raises_value_error_when_policy_is_none(self) -> None:
        state = PipelineState(phase="development_commit")
        advanced = PipelineState(phase="development")
        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            apply_commit_outcome(state, advanced, skipped=False, policy=None)


# === consolidated from test_policy_validation_checkpoint_policy_mismatch_error.py ===
class TestCheckpointPolicyMismatchError:
    """Tests for CheckpointPolicyMismatchError exception."""

    def test_error_message_contains_phase(self) -> None:
        """Test that error message contains the checkpoint phase."""
        error = CheckpointPolicyMismatchError(
            checkpoint_phase="test_phase",
            valid_phases={"phase_a", "phase_b"},
        )
        assert "test_phase" in str(error)
        assert "phase_a" in str(error)
        assert "phase_b" in str(error)
        assert "--no-resume" in str(error)


# === consolidated from test_policy_validation_default_policy_loading.py ===
class TestDefaultPolicyLoading:
    """Tests for loading the default policy."""

    def test_load_default_policy_succeeds(self) -> None:
        """Test that the default policy loads without error."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.agents is not None
        assert bundle.pipeline is not None
        assert bundle.artifacts is not None

    def test_all_builtin_drains_bound(self) -> None:
        """Test that all built-in drains are bound in default agents.toml."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        expected_drains = {
            "planning",
            "development",
            "development_analysis",
            "development_commit",
        }

        actual_drains = set(bundle.agents.agent_drains.keys())
        assert expected_drains.issubset(actual_drains), (
            f"Missing drains: {expected_drains - actual_drains}"
        )

    def test_default_pipeline_entry_phase(self) -> None:
        """Test that default pipeline has planning as entry phase."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.entry_block == "developer_iteration"
        assert bundle.pipeline.entry_phase == "planning"

    def test_default_pipeline_exposes_lifecycle_completion_metadata(self) -> None:
        """Bundled defaults should be block-authored and compile lifecycle metadata."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        lifecycle = bundle.pipeline.lifecycle_phases["development_final_commit"]
        assert lifecycle.lifecycle_name == "developer_iteration"
        assert lifecycle.completion_block == "development_final_commit"
        assert lifecycle.increments_counter == "iteration"
        assert "development_commit_cleanup" in lifecycle.before_complete
        assert "development_commit" in lifecycle.before_complete
        assert "development_final_commit_cleanup" in lifecycle.before_complete

    def test_default_pipeline_terminal_phase(self) -> None:
        """Test that default pipeline has complete as terminal phase."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.terminal_phase == "complete"

    def test_default_pipeline_parallel_execution_max_work_units(self) -> None:
        """Test that default pipeline loads the work unit cap from TOML."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.phases["development"].parallelization is not None
        dev_para = bundle.pipeline.phases["development"].parallelization
        assert dev_para.max_work_units == DEFAULT_MAX_WORK_UNITS

    def test_all_pipeline_drains_are_bound(self) -> None:
        """Test that every drain used in pipeline.phases is bound in agents.agent_drains.

        This is enforced by PolicyBundle's all_pipeline_drains_are_bound validator.
        """
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        # This should not raise - the validator ensures all drains are bound
        # Skip terminal phase since it never invokes an agent
        for phase_name, phase_def in bundle.pipeline.phases.items():
            if phase_def.role == "terminal":
                continue
            assert phase_def.drain in bundle.agents.agent_drains, (
                f"Phase '{phase_name}' uses unbound drain '{phase_def.drain}'"
            )

    def test_development_commit_cleanup_phase_in_default_policy(self) -> None:
        """Test that the default policy exposes both pre-analysis and final commit cleanups."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        pre_analysis_cleanup = bundle.pipeline.phases["development_commit_cleanup"]
        final_cleanup = bundle.pipeline.phases["development_final_commit_cleanup"]
        development = bundle.pipeline.phases["development"]
        dev_analysis = bundle.pipeline.phases["development_analysis"]

        assert pre_analysis_cleanup.role == "commit_cleanup"
        assert pre_analysis_cleanup.drain == "commit"
        assert final_cleanup.role == "commit_cleanup"
        assert final_cleanup.drain == "commit"

        assert development.transitions.on_success == "development_commit_cleanup"
        assert dev_analysis.transitions.on_success == "development_final_commit_cleanup"
        assert dev_analysis.decisions["completed"].target == "development_final_commit_cleanup"

        for phase in (pre_analysis_cleanup, final_cleanup):
            assert phase.loop_policy is not None, "commit cleanup phases must declare a loop_policy"
            assert phase.loop_policy.iteration_state_field == "commit_cleanup_iteration", (
                f"loop_policy.iteration_state_field must be 'commit_cleanup_iteration', "
                f"got: {phase.loop_policy.iteration_state_field}"
            )

    def test_default_policy_uses_pre_analysis_and_final_commit_paths(self) -> None:
        """Default policy should commit before analysis and again after successful analysis."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        development = bundle.pipeline.phases["development"]
        development_analysis = bundle.pipeline.phases["development_analysis"]
        pre_analysis_cleanup = bundle.pipeline.phases["development_commit_cleanup"]
        final_cleanup = bundle.pipeline.phases["development_final_commit_cleanup"]
        final_commit = bundle.pipeline.phases["development_final_commit"]

        assert development.transitions.on_success == "development_commit_cleanup"
        pre_analysis_commit = bundle.pipeline.phases["development_commit"]

        assert pre_analysis_cleanup.transitions.on_success == "development_commit"
        assert development_analysis.transitions.on_success == "development_final_commit_cleanup"
        assert (
            development_analysis.decisions["completed"].target == "development_final_commit_cleanup"
        )
        assert final_cleanup.transitions.on_success == "development_final_commit"
        assert pre_analysis_commit.commit_policy is not None
        assert pre_analysis_commit.commit_policy.increments_counter is None
        assert pre_analysis_commit.commit_policy.skipped_advances_progress is False
        assert final_commit.role == "commit"
        assert final_commit.drain == "development_commit"
        assert final_commit.commit_policy is not None
        assert final_commit.commit_policy.skipped_advances_progress is True
        assert final_commit.commit_policy.increments_counter == "iteration"

    def test_commit_loop_resets_follow_compiled_hook_semantics(self) -> None:
        """Pre-analysis and final commit hooks reset only the counters they own."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        pre_analysis_commit = bundle.pipeline.phases["development_commit"]
        assert pre_analysis_commit.commit_policy is not None
        assert pre_analysis_commit.commit_policy.loop_resets == ["commit_cleanup_iteration"]

        final_commit = bundle.pipeline.phases["development_final_commit"]
        assert final_commit.commit_policy is not None
        loop_resets = final_commit.commit_policy.loop_resets
        assert "commit_cleanup_iteration" in loop_resets
        assert "development_analysis_iteration" in loop_resets


# === consolidated from test_policy_validation_forbid_sibling_drain_inference.py ===
class TestForbidSiblingDrainInference:
    """Tests for forbid_sibling_drain_inference validation."""

    def test_validate_drain_contracts_all_bound_passes(self) -> None:
        """Test that all drains explicitly bound passes validation with flag True."""
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
                "development_commit": AgentChainConfig(agents=["claude"]),
                "review": AgentChainConfig(agents=["claude"]),
                "review_analysis": AgentChainConfig(agents=["claude"]),
                "fix": AgentChainConfig(agents=["claude"]),
                "review_commit": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
                "development": AgentDrainConfig(chain="development", drain_class="development"),
                "development_analysis": AgentDrainConfig(
                    chain="development_analysis", drain_class="analysis"
                ),
                "development_commit": AgentDrainConfig(
                    chain="development_commit", drain_class="commit"
                ),
                "review": AgentDrainConfig(chain="review", drain_class="review"),
                "review_analysis": AgentDrainConfig(
                    chain="review_analysis", drain_class="analysis"
                ),
                "fix": AgentDrainConfig(chain="fix", drain_class="fix"),
                "review_commit": AgentDrainConfig(chain="review_commit", drain_class="commit"),
            },
        )

        # Build minimal bundle for validation
        # Use minimal pipeline that references all drains
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="development_analysis"),
                ),
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(on_success="development_commit"),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    transitions=PhaseTransition(on_success="review_analysis"),
                ),
                "review_analysis": PhaseDefinition(
                    drain="review_analysis",
                    transitions=PhaseTransition(on_success="review_commit"),
                ),
                "fix": PhaseDefinition(
                    drain="fix",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review_commit": PhaseDefinition(
                    drain="review_commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
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
        bundle = PolicyBundle(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        # Should not raise
        validate_drain_contracts(bundle)

    def test_validate_drain_contracts_ignores_unused_canonical_drains(self) -> None:
        """Drain validation only checks drains used by the active pipeline.

        When forbid_sibling_drain_inference=True, only non-terminal pipeline phases'
        drains need explicit bindings. Unused canonical drains (review, review_analysis,
        etc.) do NOT need to be bound if they are not referenced in the pipeline.
        """
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "development": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
                "development": AgentDrainConfig(chain="development", drain_class="development"),
                # review, review_analysis, review_commit, fix are intentionally NOT bound
                # but they are also NOT in the pipeline - so no error is expected
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
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
        bundle = PolicyBundle(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        # Should NOT raise: review etc. are not in the pipeline, so not required
        validate_drain_contracts(bundle)

    def test_validate_drain_contracts_pipeline_drain_unbound_raises(self) -> None:
        """When a drain used by a non-terminal pipeline phase is unbound, raises."""
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
                # development_analysis is in the pipeline but NOT bound
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development_analysis"),
                ),
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        artifacts = ArtifactsPolicy(artifacts={})
        # Use model_construct to bypass all_pipeline_drains_are_bound so we can
        # test validate_drain_contracts in isolation
        bundle = PolicyBundle.model_construct(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        with pytest.raises(
            PolicyValidationError, match="pipeline drains lack explicit chain bindings"
        ):
            validate_drain_contracts(bundle)

    def test_validate_drain_contracts_flag_false_skips_validation(self) -> None:
        """Test that forbid_sibling_drain_inference=False skips validation."""
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=False,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
                # No other drains bound - this would fail with flag True
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
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
        bundle = PolicyBundle(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        # Should not raise even though drains are unbound
        validate_drain_contracts(bundle)

    def test_forbid_sibling_drain_inference_default_false(self) -> None:
        """Test that forbid_sibling_drain_inference defaults to False."""
        policy = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )
        assert policy.forbid_sibling_drain_inference is False


# === consolidated from test_policy_validation_get_drain_resolution_matrix.py ===
class TestGetDrainResolutionMatrix:
    """Tests for get_drain_resolution_matrix."""

    def test_empty_matrix(self) -> None:
        """Test empty bundle returns empty matrix."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        matrix = get_drain_resolution_matrix(bundle)
        assert isinstance(matrix, dict)
        # Should have entries since default policy has bound drains
        assert len(matrix) > 0

    def test_matrix_contains_drain_info(self) -> None:
        """Test that matrix contains correct drain information."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        matrix = get_drain_resolution_matrix(bundle)

        if "planning" in matrix:
            assert "chain" in matrix["planning"]
            assert "agents" in matrix["planning"]
            assert "max_retries" in matrix["planning"]


# === consolidated from test_policy_validation_load_policy_forbid_sibling_inference.py ===
class TestLoadPolicyForbidSiblingInference:
    """Tests that load_policy enforces forbid_sibling_drain_inference."""

    def test_load_policy_rejects_missing_drains(self, tmp_path: Path) -> None:
        """load_policy rejects a pipeline drain bound nowhere — no inference.

        When forbid_sibling_drain_inference=True, pipeline-used drains must be
        explicitly bound. Drains the user omits but the bundled defaults bind
        are satisfied by layering; a drain unknown to BOTH the user policy and
        the bundled defaults is rejected at load time.
        """
        config_dir = tmp_path / ".agent"
        config_dir.mkdir(parents=True)

        agents_toml = dedent(
            """
            forbid_sibling_drain_inference = true

            [agent_chains.planning]
            agents = ["claude"]

            [agent_drains.planning]
            chain = "planning"
            drain_class = "planning"
            # custom_analysis drain intentionally absent everywhere
            """
        )
        (config_dir / "agents.toml").write_text(agents_toml)

        pipeline_toml = dedent(
            """
            [phases.planning]
            drain = "planning"
            role = "execution"
            [phases.planning.transitions]
            on_success = "custom_analysis"

            [phases.custom_analysis]
            drain = "custom_analysis"
            role = "execution"
            [phases.custom_analysis.transitions]
            on_success = "complete"

            [phases.complete]
            drain = "planning"
            role = "terminal"
            terminal_outcome = "success"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"

            entry_phase = "planning"
            terminal_phase = "complete"
            """
        )
        (config_dir / "pipeline.toml").write_text(pipeline_toml)

        with pytest.raises(
            LoaderPolicyValidationError,
            match="unbound drains",
        ):
            load_policy(config_dir)


# === consolidated from test_policy_validation_pipeline_owned_artifact_required_policy.py ===
class TestPipelineOwnedArtifactRequiredPolicy:
    """Tests for pipeline-owned required artifact behavior."""

    def test_default_policy_loads_with_development_phase_required(self, tmp_path: Path) -> None:
        """Default policy must load with development artifact requirement owned by pipeline."""
        bundle = load_policy(tmp_path / ".agent")
        assert bundle.pipeline.phases["development"].artifact_required is True, (
            "phases.development.artifact_required must be True in default policy"
        )

    def test_artifact_contract_rejects_phase_owned_artifact_required(self) -> None:
        """ArtifactContract must reject artifact_required because it belongs to pipeline.toml."""
        with pytest.raises(ValueError, match=r"pipeline\.toml"):
            ArtifactContract.model_validate(
                {
                    "drain": "development",
                    "artifact_type": "development_result",
                    "artifact_required": False,
                }
            )

    def test_artifact_contract_does_not_publish_retired_json_path_override(self) -> None:
        assert "artifact_json_path" not in ArtifactContract.model_json_schema()["properties"]

    def test_required_artifact_regression_uses_format_neutral_artifact_path(self) -> None:
        """Cover the markdown-migration task: the model exposes no JSON-named path."""
        field_names = {field.name for field in fields(RequiredArtifact)}

        assert "artifact_path" in field_names
        assert "json_path" not in field_names

    def test_phase_required_artifact_uses_pipeline_owned_required_flag(
        self, tmp_path: Path
    ) -> None:
        """resolve_phase_required_artifact threads artifact_required from phase policy."""

        bundle = load_policy(tmp_path / ".agent")
        dev_ra = resolve_phase_required_artifact(
            bundle.pipeline,
            bundle.artifacts,
            phase="development",
            drain="development",
        )
        assert dev_ra is not None, "development phase must have a RequiredArtifact entry"
        assert dev_ra.artifact_required is True, (
            "RequiredArtifact for development must have artifact_required=True from pipeline"
        )


# === consolidated from test_policy_validation_policy_bundle_validation.py ===
class TestPolicyBundleValidation:
    """Tests for cross-policy validation in PolicyBundle."""

    def test_pipeline_drain_not_bound_raises(self) -> None:
        """Test that a pipeline using an unbound drain raises ValueError."""

        # Create agents policy with no development drain bound
        agents = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )

        # Create pipeline that uses development drain (not bound)
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",  # Not bound in agents!
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
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
        with pytest.raises(ValueError, match="unbound drains"):
            PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)

    def test_analysis_phase_without_vocabulary_raises(self) -> None:
        """Test that role='analysis' phase without decision_vocabulary raises."""
        # Create valid agents policy
        agents = AgentsPolicy(
            agent_chains={
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
            },
        )

        # Create pipeline with analysis phase
        pipeline = PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development",
            terminal_phase="complete",
        )

        # Without an artifacts policy that provides decision_vocabulary,
        # this should raise
        artifacts = ArtifactsPolicy(artifacts={})
        with pytest.raises(ValueError, match="decision_vocabulary"):
            PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)


# === consolidated from test_policy_validation_shared_drain_history_consistency.py ===
class TestSharedDrainHistoryConsistency:
    """Tests for _validate_shared_drain_history_consistency via validate_policy_completeness."""

    def _minimal_bundle(
        self,
        *,
        history_a: ArtifactHistoryPolicy | None,
        history_b: ArtifactHistoryPolicy | None,
    ) -> PolicyBundle:
        agents = AgentsPolicy(
            agent_chains={"mychain": AgentChainConfig(agents=["claude"])},
            agent_drains={"shared_drain": AgentDrainConfig(chain="mychain")},
        )
        pipeline = PipelinePolicy(
            entry_phase="phase_a",
            terminal_phase="complete",
            phases={
                "phase_a": PhaseDefinition(
                    drain="shared_drain",
                    role="execution",
                    transitions=PhaseTransition(on_success="phase_b", on_failure="failed_terminal"),
                    artifact_history=history_a,
                ),
                "phase_b": PhaseDefinition(
                    drain="shared_drain",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete", on_failure="failed_terminal"
                    ),
                    artifact_history=history_b,
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="failed_terminal",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
            },
            recovery=RecoveryPolicy(failed_route="failed_terminal"),
        )
        return PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )

    def test_conflicting_history_enabled_on_same_drain_raises(self) -> None:
        """Two phases on the same drain with conflicting artifact_history.enabled raise error."""

        bundle = self._minimal_bundle(
            history_a=ArtifactHistoryPolicy(enabled=True),
            history_b=ArtifactHistoryPolicy(enabled=False),
        )

        with pytest.raises(PolicyValidationError, match=r"artifact_history\.enabled"):
            validate_policy_completeness(bundle)

    def test_consistent_history_enabled_on_same_drain_passes(self) -> None:
        """Two phases on the same drain with the same artifact_history.enabled pass."""

        bundle = self._minimal_bundle(
            history_a=ArtifactHistoryPolicy(enabled=True),
            history_b=ArtifactHistoryPolicy(enabled=True),
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_no_artifact_history_on_phase_is_excluded_from_check(self) -> None:
        """A phase with no artifact_history declared is excluded from drain consistency."""

        bundle = self._minimal_bundle(
            history_a=ArtifactHistoryPolicy(enabled=True),
            history_b=None,
        )
        validate_policy_completeness(bundle)  # must not raise


# === consolidated from test_policy_validation_strict_cli_counter_overrides.py ===
class TestCliCounterOverrides:
    """CLI counter overrides must reference declared budget_counters."""

    def _bundle_with_budget_counter(self, counter_name: str) -> PolicyBundle:
        agents = _strict_cli_counter_overrides_minimal_agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": _strict_cli_counter_overrides_terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={counter_name: BudgetCounterConfig(tracks_budget=False, default_max=0)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        return PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )

    def test_unknown_counter_override_raises(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        with pytest.raises(PolicyValidationError, match="unknown_counter"):
            validate_policy_completeness(bundle, cli_counter_overrides={"unknown_counter": 3})

    def test_declared_counter_override_passes(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(
            bundle, cli_counter_overrides={"my_counter": 3}
        )  # must not raise

    def test_no_overrides_kwarg_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle)  # no cli_counter_overrides — must not raise

    def test_none_overrides_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle, cli_counter_overrides=None)  # must not raise

    def test_error_message_lists_declared_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("declared_counter")
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle, cli_counter_overrides={"bad_counter": 1})
        error_msg = str(exc_info.value)
        assert "bad_counter" in error_msg
        assert "declared_counter" in error_msg


# === consolidated from test_policy_validation_strict_legacy_fields_rejected.py ===
class TestLegacyFieldsRejected:
    """Tests for removed/legacy fields that must be rejected at construction time."""

    def test_failed_route_failed_alias_rejected(self) -> None:
        """RecoveryPolicy rejects 'failed' as failed_route (removed pseudo-phase alias)."""
        with pytest.raises(ValueError, match="'failed' is no longer accepted"):
            RecoveryPolicy(failed_route="failed")

    def test_drain_class_substring_inference_rejected(self) -> None:
        """Custom drain without drain_class raises PolicyValidationError.

        Pre-v2 behavior silently inferred drain class from name substrings
        (e.g. a drain named 'custom_fixer_drain' resolved to DrainClass.FIX).
        That inference was removed; an explicit drain_class is required.
        """
        agents = AgentsPolicy(
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"custom_fixer_drain": AgentDrainConfig(chain="chain")},
        )
        with pytest.raises(PolicyValidationError):
            drain_class_for_session("custom_fixer_drain", agents)

    def test_drain_class_missing_rejected_under_strict_validation(self) -> None:
        """validate_drain_contracts rejects drains missing drain_class when strict.

        When forbid_sibling_drain_inference=true, every pipeline-used drain must
        declare drain_class explicitly in agents.toml.
        """
        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain"),
                "complete": AgentDrainConfig(chain="chain"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(PolicyValidationError, match="no explicit drain_class"):
            validate_drain_contracts(bundle)

    def test_drain_class_present_passes_strict_validation(self) -> None:
        """validate_drain_contracts passes when all drains have explicit drain_class."""
        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain", drain_class="development"),
                "complete": AgentDrainConfig(chain="chain", drain_class="development"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        validate_drain_contracts(bundle)  # must not raise

    def test_legacy_phase_field_requires_commit_rejected(self) -> None:
        """PhaseDefinition with requires_commit=True is rejected with an actionable error."""
        with pytest.raises(ValidationError, match="requires_commit has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                requires_commit=True,
                transitions=PhaseTransition(on_success="done"),
            )

    def test_legacy_phase_field_embeds_analysis_rejected(self) -> None:
        """PhaseDefinition with embeds_analysis=True is rejected with an actionable error."""
        with pytest.raises(ValidationError, match="embeds_analysis has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                embeds_analysis=True,
                transitions=PhaseTransition(on_success="done"),
            )


# === consolidated from test_policy_validation_strict_parallelization_consistency.py ===
class TestParallelizationConsistency:
    """parallelization.max_work_units must be >= max_parallel_workers."""

    def test_max_work_units_less_than_max_parallel_workers_raises(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=5,
                    max_work_units=3,
                ),
            ),
            "complete": _strict_parallelization_consist_terminal_phase(),
        }
        bundle = _strict_parallelization_consist_minimal_bundle_with_phases(phases)
        with pytest.raises(
            PolicyValidationError,
            match=r"max_work_units.*must be >=.*max_parallel_workers",
        ):
            validate_policy_completeness(bundle)

    def test_max_work_units_equal_to_max_parallel_workers_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=4,
                    max_work_units=4,
                ),
            ),
            "complete": _strict_parallelization_consist_terminal_phase(),
        }
        bundle = _strict_parallelization_consist_minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_max_work_units_greater_than_max_parallel_workers_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=2,
                    max_work_units=10,
                ),
            ),
            "complete": _strict_parallelization_consist_terminal_phase(),
        }
        bundle = _strict_parallelization_consist_minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_phase_without_parallelization_is_not_checked(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _strict_parallelization_consist_terminal_phase(),
        }
        bundle = _strict_parallelization_consist_minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise


# === consolidated from test_policy_validation_strict_skip_invocation_has_on_success.py ===
class TestSkipInvocationHasOnSuccess:
    """skip_invocation=true requires transitions.on_success."""

    def test_skip_invocation_without_on_success_raises(self) -> None:
        # Use model_construct to bypass Pydantic's required-field and cross-reference
        # validators so we can create the exact invalid state the policy validator
        # is designed to catch (skip_invocation=True with no on_success target).
        invalid_transitions = PhaseTransition.model_construct(
            on_success=None,
            on_failure=None,
            on_loopback=None,
        )
        work_phase = PhaseDefinition.model_construct(
            drain="work",
            transitions=invalid_transitions,
            role="execution",
            skip_invocation=True,
            bypass_routes={},
            decisions={},
            verification=None,
            parallelization=None,
        )
        policy = PipelinePolicy.model_construct(
            phases={"work": work_phase, "complete": _strict_skip_invocation_has_on__terminal_phase()},
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
            loop_counters={},
            budget_counters={},
            post_commit_routes=[],
        )
        bundle = PolicyBundle.model_construct(
            agents=_strict_skip_invocation_has_on__minimal_agents(["work", "complete"]),
            pipeline=policy,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(
            PolicyValidationError,
            match=r"skip_invocation=true requires transitions\.on_success",
        ):
            validate_policy_completeness(bundle)

    def test_skip_invocation_true_with_on_success_set_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=True,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _strict_skip_invocation_has_on__terminal_phase(),
        }
        bundle = _strict_skip_invocation_has_on__minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_skip_invocation_false_without_on_success_does_not_raise_from_this_check(
        self,
    ) -> None:
        """skip_invocation=False with no on_success may still fail other validators."""
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=False,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _strict_skip_invocation_has_on__terminal_phase(),
        }
        bundle = _strict_skip_invocation_has_on__minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise due to skip_invocation check


# === consolidated from test_policy_validation_validate_chain_exists.py ===
class TestValidateChainExists:
    """Tests for validate_chain_exists."""

    def test_chain_exists(self) -> None:
        """Test that an existing chain passes validation."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        # Should not raise
        validate_chain_exists("development", bundle)

    def test_chain_not_defined(self) -> None:
        """Test that an undefined chain raises ValueError."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        with pytest.raises(ValueError, match="not defined"):
            validate_chain_exists("nonexistent_chain", bundle)


# === consolidated from test_policy_validation_validate_checkpoint_compatible.py ===
class TestValidateCheckpointCompatible:
    """Tests for validate_checkpoint_compatible."""

    def test_checkpoint_compatible(self) -> None:
        """Test that a compatible checkpoint passes validation."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        # Should not raise
        validate_checkpoint_compatible("planning", bundle)

    def test_checkpoint_incompatible(self) -> None:
        """Test that an incompatible checkpoint raises error."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        with pytest.raises(CheckpointPolicyMismatchError):
            validate_checkpoint_compatible("nonexistent_phase", bundle)


# === consolidated from test_policy_validation_validate_drain_bound.py ===
class TestValidateDrainBound:
    """Tests for validate_drain_bound."""

    def test_drain_bound(self) -> None:
        """Test that a bound drain passes validation."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        # Should not raise
        validate_drain_bound("planning", bundle)

    # ponytail: policy loading under xdist can exceed 1s; the 60s suite budget remains authoritative.
    @pytest.mark.timeout_seconds(2.0)
    def test_drain_not_bound(self) -> None:
        """Test that an unbound drain raises ValueError."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        with pytest.raises(ValueError, match="not bound"):
            validate_drain_bound("nonexistent_drain", bundle)


# === consolidated from test_policy_validation_validate_phase_exists_in_policy.py ===
class TestValidatePhaseExistsInPolicy:
    """Tests for validate_phase_exists_in_policy."""

    def test_phase_exists_in_policy(self) -> None:
        """Test that an existing phase passes validation."""
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )
        # Should not raise
        validate_phase_exists_in_policy("development", pipeline)

    def test_phase_not_in_policy(self) -> None:
        """Test that a missing phase raises CheckpointPolicyMismatchError."""
        # Use mock to avoid Pydantic validation complexity
        pipeline = MagicMock()
        pipeline.phases = {
            "planning": MagicMock(),
            "development": MagicMock(),
            "review": MagicMock(),
        }

        with pytest.raises(CheckpointPolicyMismatchError) as exc_info:
            validate_phase_exists_in_policy("nonexistent_phase", pipeline)
        assert exc_info.value.checkpoint_phase == "nonexistent_phase"


# === consolidated from test_policy_validation_validate_policy_completeness_new_rules.py ===
class TestValidatePolicyCompletenessNewRules:
    """Tests for vocab superset check, commit_policy, loop_resets, and failed_route."""

    def _minimal_agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
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

    def test_execution_role_rejects_loop_policy(self) -> None:
        agents = self._minimal_agents(["development", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                    loop_policy=PhaseLoopPolicy(iteration_state_field="development_iteration"),
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
            loop_counters={"development_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development",
            terminal_phase="complete",
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )

        with pytest.raises(
            PolicyValidationError,
            match="loop_policy is only valid for role='analysis' or role='commit_cleanup'",
        ):
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

    def test_commit_policy_loop_resets_accepts_commit_cleanup_iteration_field(self) -> None:
        """commit_policy.loop_resets accepting commit_cleanup iteration field passes validation.

        Bug #1: The validator only scanned analysis-role phases for valid iteration
        fields. This test verifies that commit_cleanup-role phases are also accepted.
        """
        drains = [
            "development_analysis",
            "commit",
            "development_commit_cleanup",
            "development_commit",
            "complete",
        ]
        agents = self._minimal_agents(drains)
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis",
                    "development_analysis_iteration",
                    on_success="development_commit_cleanup",
                    failure_target="failed_terminal",
                ),
                "development_commit_cleanup": PhaseDefinition(
                    drain="commit",
                    role="commit_cleanup",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development_commit_cleanup",
                        on_failure="failed_terminal",
                    ),
                    loop_policy=PhaseLoopPolicy(iteration_state_field="commit_cleanup_iteration"),
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
                        loop_resets=["commit_cleanup_iteration"],
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
            loop_counters={
                "development_analysis_iteration": LoopCounterConfig(default_max=3),
                "commit_cleanup_iteration": LoopCounterConfig(default_max=3),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)  # must not raise

    def test_commit_policy_loop_resets_rejects_field_not_from_analysis_or_commit_cleanup(
        self,
    ) -> None:
        """commit_policy.loop_resets with field from unknown role fails validation.

        Bug #1: The validator only checked analysis-role phases. This test ensures
        that fields from unrecognized roles are still rejected.
        """
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
                        # "unknown_iteration_field" is not from any analysis or commit_cleanup phase
                        loop_resets=["unknown_iteration_field"],
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


# === consolidated from test_policy_validation_validate_policy_completeness_reachability.py ===
class TestValidatePolicyCompletenessReachability:
    """Tests for phase reachability validation in validate_policy_completeness.

    Every phase declared in policy.phases must be reachable from entry_phase
    following any combination of transitions (on_success, on_failure, on_loopback,
    decisions, bypass_routes). Orphaned phases that can never be reached from the
    entry point are rejected as incomplete policy.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
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


# === consolidated from test_policy_validation_validate_policy_completeness_verification_role.py ===
class TestValidatePolicyCompletenessVerificationRole:
    """Tests for validation of role='verification' phases in validate_policy_completeness."""

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_verification_role_requires_verification_block(self) -> None:
        """role='verification' with no verification block fails completeness check."""
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    transitions=PhaseTransition(on_success="complete"),
                    # verification intentionally absent
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="requires a verification block"):
            validate_policy_completeness(bundle)

    def test_verification_kind_pydantic_rejects_invalid(self) -> None:
        """PhaseVerificationPolicy rejects invalid kind values."""
        with pytest.raises(ValidationError):
            PhaseVerificationPolicy(**{"kind": "bogus", "gate_for": "advancement"})

    def test_verification_gate_for_pydantic_rejects_invalid(self) -> None:
        """PhaseVerificationPolicy rejects invalid gate_for values."""
        with pytest.raises(ValidationError):
            PhaseVerificationPolicy(**{"kind": "none", "gate_for": "unknown_gate"})

    def test_verification_on_failure_route_unknown_phase_rejected(self) -> None:
        """on_failure_route naming a missing phase fails completeness check."""
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="nonexistent_phase",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="nonexistent_phase"):
            validate_policy_completeness(bundle)

    def test_verification_on_failure_route_failed_undeclared_rejected(self) -> None:
        """on_failure_route to undeclared 'failed' pseudo-phase is now rejected.

        The bare 'failed' alias is no longer a valid pseudo-phase target.
        Declare a phase with role='terminal' and terminal_outcome='failure' and
        reference it via on_failure_route.
        """
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="none",
                        gate_for="advancement",
                        on_failure_route="failed",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="'failed' is not a declared phase"):
            validate_policy_completeness(bundle)

    def test_verification_on_failure_route_legacy_pseudo_rejected(self) -> None:
        """on_failure_route to 'phase_failed' or 'exit_failure' pseudo-phases is rejected."""
        for pseudo in ("phase_failed", "exit_failure"):
            agents = self._agents(["verify", "complete"])
            pipeline = PipelinePolicy(
                phases={
                    "verify": PhaseDefinition(
                        drain="verify",
                        role="verification",
                        verification=PhaseVerificationPolicy(
                            kind="none",
                            gate_for="advancement",
                            on_failure_route=pseudo,
                        ),
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                    "complete": self._terminal_phase(),
                },
                entry_phase="verify",
                terminal_phase="complete",
                recovery=RecoveryPolicy(failed_route="complete"),
            )
            bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
            with pytest.raises(PolicyValidationError, match=pseudo):
                validate_policy_completeness(bundle)

    def test_verification_on_failure_route_declared_terminal_phase_accepted(self) -> None:
        """on_failure_route pointing to a declared terminal phase passes."""
        agents = self._agents(["verify", "crashed", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="crashed",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "crashed": PhaseDefinition(
                    drain="crashed",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)  # must not raise

    def test_verification_with_valid_block_passes(self) -> None:
        """role='verification' with valid block passes completeness check."""
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="none",
                        gate_for="advancement",
                        on_failure_route=None,
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)  # must not raise


# === consolidated from test_policy_validation_validate_post_commit_all_budget_states_covered.py ===
class TestValidatePostCommitAllBudgetStatesCovered:
    """Tests for _validate_post_commit_routes_complete requiring all three budget states.

    When a commit phase increments a tracked budget counter, post_commit_routes must
    cover all three budget states (remaining, exhausted, no_review) so the runtime
    always has an unambiguous route after commit.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_success(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def _terminal_failure(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="crashed",
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
        )

    def _bundle_with_routes(self, routes: list[tuple[str, str]]) -> PolicyBundle:
        agents = self._agents(["work", "commit", "complete", "crashed"])
        post_commit = [
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="commit", budget_state=state),
                target=target,
            )
            for state, target in routes
        ]
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="crashed",
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_success(),
                "crashed": self._terminal_failure(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            post_commit_routes=post_commit,
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        return PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )

    def test_missing_remaining_state_raises(self) -> None:
        """Only exhausted+no_review present: remaining missing → validation fails."""
        bundle = self._bundle_with_routes(
            [
                ("exhausted", "complete"),
                ("no_review", "complete"),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "remaining" in str(exc_info.value)

    def test_missing_exhausted_state_raises(self) -> None:
        """Only remaining+no_review present: exhausted missing → validation fails."""
        bundle = self._bundle_with_routes(
            [
                ("remaining", "work"),
                ("no_review", "complete"),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "exhausted" in str(exc_info.value)

    def test_missing_no_review_state_raises(self) -> None:
        """Only remaining+exhausted present: no_review missing → validation fails."""
        bundle = self._bundle_with_routes(
            [
                ("remaining", "work"),
                ("exhausted", "complete"),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "no_review" in str(exc_info.value)

    def test_all_three_budget_states_passes(self) -> None:
        """All three budget states declared: validation passes."""
        bundle = self._bundle_with_routes(
            [
                ("remaining", "work"),
                ("exhausted", "complete"),
                ("no_review", "complete"),
            ]
        )
        validate_policy_completeness(bundle)  # must not raise


# === consolidated from test_policy_validation_validate_post_commit_routes_coverage.py ===
class TestValidatePostCommitRoutesCoverage:
    """Tests for post_commit_routes coverage validation.

    A commit-role phase that increments a tracked budget counter must have at
    least one [[post_commit_routes]] entry. Missing routes allow silent
    fall-through on on_success, which is false configurability.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_post_commit_routes_required_for_tracked_counter(self) -> None:
        """Commit phase with tracked budget counter and no matching post_commit_routes fails."""
        agents = self._agents(["work", "commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        error_msg = str(exc_info.value)
        assert "no post_commit_routes apply to this phase" in error_msg
        assert "commit" in error_msg

    def test_post_commit_routes_present_for_tracked_counter_passes(self) -> None:
        """Commit phase with tracked counter AND all three budget states declared passes."""
        agents = self._agents(["work", "commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            post_commit_routes=[
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="remaining"),
                    target="work",
                ),
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="exhausted"),
                    target="complete",
                ),
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="no_review"),
                    target="complete",
                ),
            ],
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_untracked_counter_does_not_require_post_commit_routes(self) -> None:
        """Commit phase with untracked (tracks_budget=False) counter needs no routes."""
        agents = self._agents(["work", "commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=False, default_max=0)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise


# === consolidated from test_policy_validation_validate_required_inputs.py ===
class TestValidateRequiredInputs:
    """Tests for validate_required_inputs."""

    def test_missing_prompt_md_raises_with_init_hint(self, tmp_path: Path) -> None:
        """Missing PROMPT.md error must mention both the structural prefix and ralph --init."""
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_required_inputs(scope)
        msg = str(exc_info.value)
        assert "Required input file not found" in msg
        assert "ralph --init" in msg

    def test_present_prompt_md_does_not_raise(self, tmp_path: Path) -> None:
        """A non-empty PROMPT.md passes validation without error."""
        (tmp_path / "PROMPT.md").write_text("# Goal\n\nDo something.\n")
        scope = MagicMock()
        scope.root = tmp_path
        validate_required_inputs(scope)  # should not raise

    def test_empty_prompt_md_raises(self, tmp_path: Path) -> None:
        """An empty PROMPT.md raises PolicyValidationError."""
        (tmp_path / "PROMPT.md").write_text("")
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError, match="empty"):
            validate_required_inputs(scope)

    def test_starter_sentinel_prompt_md_raises(self, tmp_path: Path) -> None:
        """A PROMPT.md with the starter sentinel raises PolicyValidationError."""
        (tmp_path / "PROMPT.md").write_text(
            STARTER_PROMPT_SENTINEL + "\n\n# Goal\n\nExample body\n"
        )
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_required_inputs(scope)
        msg = str(exc_info.value)
        assert "starter template" in msg
        assert "ralph" in msg
        assert str(tmp_path) in msg

    def test_edited_prompt_md_passes(self, tmp_path: Path) -> None:
        """A PROMPT.md without the sentinel passes validation."""
        (tmp_path / "PROMPT.md").write_text("# Goal\n\nBuild a real feature here.\n")
        scope = MagicMock()
        scope.root = tmp_path
        validate_required_inputs(scope)  # must not raise

    def test_sentinel_anywhere_in_prompt_raises(self, tmp_path: Path) -> None:
        """Sentinel on any line in PROMPT.md raises PolicyValidationError."""
        (tmp_path / "PROMPT.md").write_text(
            "# Goal\n\nMy task.\n\n" + STARTER_PROMPT_SENTINEL + "\n"
        )
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError):
            validate_required_inputs(scope)


# === consolidated from test_policy_validation_validate_review_phase_outcome_complete.py ===
class TestValidateReviewPhaseOutcomeComplete:
    """Tests for _validate_review_phase_outcome_complete in validate_policy_completeness."""

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_clean_outcome_missing_from_bypass_routes_fails(self) -> None:
        """review phase with clean_outcome not in bypass_routes fails completeness."""
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="approved",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={},  # 'approved' key absent
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
        with pytest.raises(PolicyValidationError, match=r"clean_outcome.*bypass_routes"):
            validate_policy_completeness(bundle)

    def test_clean_outcome_present_in_bypass_routes_passes(self) -> None:
        """review phase with clean_outcome key in bypass_routes passes completeness."""
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="approved",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={"approved": "complete"},
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

    def test_review_phase_without_clean_outcome_skipped(self) -> None:
        """review phase with no clean_outcome set is not checked."""
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    transitions=PhaseTransition(on_success="complete"),
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


# === consolidated from test_policy_validation_validate_terminal_failure_phase_required.py ===
class TestValidateTerminalFailurePhaseRequired:
    """Tests for _validate_terminal_failure_phase_declared.

    When any phase declares on_failure or verification.on_failure_route transitions,
    at least one phase with role='terminal' and terminal_outcome='failure' must exist
    so the runtime has a policy-declared failure destination.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_success(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def _terminal_failure(self, drain: str = "crashed") -> PhaseDefinition:
        return PhaseDefinition(
            drain=drain,
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success=drain, on_loopback=drain),
        )

    def test_on_failure_without_terminal_failure_phase_raises(self) -> None:
        """Policy with on_failure route but no terminal-failure phase fails validation."""
        agents = self._agents(["work", "fallback", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="fallback",
                    ),
                ),
                "fallback": PhaseDefinition(
                    drain="fallback",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "terminal_outcome='failure'" in str(exc_info.value)

    def test_verification_failure_route_without_terminal_failure_phase_raises(self) -> None:
        """Policy with verification.on_failure_route but no terminal-failure phase fails."""
        agents = self._agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="complete",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "terminal_outcome='failure'" in str(exc_info.value)

    def test_on_failure_with_terminal_failure_phase_passes(self) -> None:
        """Policy with on_failure route AND terminal-failure phase passes validation."""
        agents = self._agents(["work", "fallback", "complete", "crashed"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
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
                "complete": self._terminal_success(),
                "crashed": self._terminal_failure(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_no_failure_routes_skips_terminal_failure_check(self) -> None:
        """Policy with no on_failure routes does not require a terminal-failure phase."""
        agents = self._agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise


# === consolidated from test_policy_validation_validate_work_units_against_policy.py ===
class TestValidateWorkUnitsAgainstPolicy:
    """Tests for planning work_units policy validation."""

    def _minimal_pipeline(
        self,
        *,
        parallelization: PhaseParallelization | None = None,
    ) -> PipelinePolicy:
        return PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                    parallelization=parallelization,
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

    def test_multi_work_units_requires_parallel_execution_policy(self) -> None:
        pipeline = self._minimal_pipeline()
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["tests"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="parallelization"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_multi_work_units_respects_max_parallel_workers(self) -> None:
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=1)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["tests"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="max_parallel_workers"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_work_units_count_cap_exceeded(self) -> None:
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {
                        "unit_id": f"u{i}",
                        "description": f"Work unit {i}",
                        "allowed_directories": ["src"],
                    }
                    for i in range(51)
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="exceeds cap"):
            validate_work_units_against_policy(work_units, bundle.pipeline, phase="development")

    def test_work_units_count_cap_custom(self) -> None:
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(
                max_parallel_workers=8,
                max_work_units=3,
            )
        )

        allowed_work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {
                        "unit_id": f"u{i}",
                        "description": f"Work unit {i}",
                        "allowed_directories": [f"dir{i}"],
                    }
                    for i in range(3)
                ]
            }
        )
        assert allowed_work_units is not None

        validate_work_units_against_policy(allowed_work_units, pipeline, phase="planning")

        rejected_work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {
                        "unit_id": f"u{i}",
                        "description": f"Work unit {i}",
                        "allowed_directories": [f"dir{i}"],
                    }
                    for i in range(4)
                ]
            }
        )
        assert rejected_work_units is not None

        with pytest.raises(PolicyValidationError, match="exceeds cap"):
            validate_work_units_against_policy(rejected_work_units, pipeline, phase="planning")

    def test_overlapping_edit_areas_raise_policy_validation_error(self) -> None:
        """Work units with overlapping allowed_directories must raise PolicyValidationError."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["src/subdir"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="overlaps"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_missing_allowed_directories_raises_policy_validation_error(self) -> None:
        """Work units without allowed_directories must raise PolicyValidationError."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B"},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="allowed_directories"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_disjoint_edit_areas_pass_validation(self) -> None:
        """Work units with disjoint allowed_directories must pass validation."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["tests"]},
                ]
            }
        )
        assert work_units is not None

        validate_work_units_against_policy(work_units, pipeline, phase="planning")  # must not raise

    def test_reserved_path_at_policy_load_raises_policy_validation_error(self) -> None:
        """Work units declaring reserved paths raise PolicyValidationError at policy load time."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": [".agent/custom"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="reserved path"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_validation_does_not_run_for_phase_without_parallelization(self) -> None:
        """A phase with no parallelization rejects multi-work-unit plans fail-closed."""
        pipeline = self._minimal_pipeline()  # planning phase has no parallelization
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    # Overlapping — but the phase-scoped error fires before the overlap check
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["src/sub"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="does not declare parallelization"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

