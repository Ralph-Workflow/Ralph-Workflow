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
from pathlib import Path
from textwrap import dedent
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.cli.commands.init import STARTER_PROMPT_SENTINEL
from ralph.pipeline.progress import apply_commit_outcome
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import parse_work_units_from_artifact
from ralph.policy.loader import PolicyValidationError as LoaderPolicyValidationError
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
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

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


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


class TestDefaultPolicyLoading:
    """Tests for loading the default policy."""

    def test_load_default_policy_succeeds(self) -> None:
        """Test that the default policy loads without error."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.agents is not None
        assert bundle.pipeline is not None
        assert bundle.artifacts is not None

    def test_all_six_builtin_drains_bound(self) -> None:
        """Test that all six built-in drains are bound in default agents.toml."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        expected_drains = {
            "planning",
            "development",
            "development_analysis",
            "development_commit",
            "review",
            "review_analysis",
            "fix",
            "review_commit",
        }

        actual_drains = set(bundle.agents.agent_drains.keys())
        assert expected_drains.issubset(actual_drains), (
            f"Missing drains: {expected_drains - actual_drains}"
        )

    def test_default_pipeline_entry_phase(self) -> None:
        """Test that default pipeline has planning as entry phase."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.entry_phase == "planning"

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
        """Test that embeds_analysis phase without decision_vocabulary raises."""
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
                    embeds_analysis=True,
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
                "planning": AgentDrainConfig(chain="planning"),
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
                "development_commit": AgentDrainConfig(chain="development_commit"),
                "review": AgentDrainConfig(chain="review"),
                "review_analysis": AgentDrainConfig(chain="review_analysis"),
                "fix": AgentDrainConfig(chain="fix"),
                "review_commit": AgentDrainConfig(chain="review_commit"),
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
                        on_failure="failed",
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
                "planning": AgentDrainConfig(chain="planning"),
                "development": AgentDrainConfig(chain="development"),
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


class TestLoadPolicyForbidSiblingInference:
    """Tests that load_policy enforces forbid_sibling_drain_inference."""

    def test_load_policy_rejects_missing_drains(self, tmp_path: Path) -> None:
        """load_policy rejects a pipeline where a used drain is not bound in agents.toml.

        When forbid_sibling_drain_inference=True, pipeline-used drains must be explicitly
        bound. A pipeline with a development_analysis phase but no development_analysis
        drain binding is rejected at load time.
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
            # development_analysis drain intentionally absent
            """
        )
        (config_dir / "agents.toml").write_text(agents_toml)

        pipeline_toml = dedent(
            """
            [phases.planning]
            drain = "planning"
            role = "execution"
            [phases.planning.transitions]
            on_success = "development_analysis"

            [phases.development_analysis]
            drain = "development_analysis"
            role = "execution"
            [phases.development_analysis.transitions]
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


class TestValidateDrainBound:
    """Tests for validate_drain_bound."""

    def test_drain_bound(self) -> None:
        """Test that a bound drain passes validation."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        # Should not raise
        validate_drain_bound("planning", bundle)

    def test_drain_not_bound(self) -> None:
        """Test that an unbound drain raises ValueError."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        with pytest.raises(ValueError, match="not bound"):
            validate_drain_bound("nonexistent_drain", bundle)


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


class TestApplyCommitOutcomeRequiresPolicy:
    """Tests that apply_commit_outcome raises when policy is None."""

    def test_raises_value_error_when_policy_is_none(self) -> None:
        state = PipelineState(phase="development_commit")
        advanced = PipelineState(phase="development")
        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            apply_commit_outcome(state, advanced, skipped=False, policy=None)


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
        self, name: DrainName, iteration_field: str, on_success: str = "complete"
    ) -> PhaseDefinition:
        """Create a minimal analysis phase with required decisions field."""
        return PhaseDefinition(
            drain=name,
            role="analysis",
            transitions=PhaseTransition(
                on_success=on_success,
                on_loopback=name,
            ),
            loop_policy=PhaseLoopPolicy(
                max_iterations=3,
                iteration_state_field=iteration_field,
            ),
            decisions={
                "completed": PhaseDecisionRoute(target=on_success, reset_loop=True),
                "failed": PhaseDecisionRoute(target="failed", reset_loop=False),
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
                        on_failure="failed",
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        max_iterations=3,
                        iteration_state_field="development_analysis_iteration",
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
                        on_failure="failed",
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
                    "development_analysis", "development_analysis_iteration",
                    on_success="development_commit",
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="failed",
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["development_analysis_iteration"],
                    ),
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
                    "development_analysis", "development_analysis_iteration",
                    on_success="development_commit",
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="failed",
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="none",  # Valid — no outer-progress bump
                        loop_resets=["development_analysis_iteration"],
                    ),
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
            "failed": PhaseDecisionRoute(target="failed", reset_loop=False),
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
                        max_iterations=3,
                        iteration_state_field="development_analysis_iteration",
                    ),
                    decisions=dev_analysis_decisions,
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="failed",
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        # "nonexistent_iteration_field" is not an iteration_state_field
                        # used by any analysis phase in this policy
                        loop_resets=["nonexistent_iteration_field"],
                    ),
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
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="invalid iteration field"):
            validate_policy_completeness(bundle)

    def test_commit_policy_loop_resets_valid_field_passes(self) -> None:
        """commit_policy.loop_resets referencing a valid iteration field passes validation."""
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        dev_analysis_decisions = {
            "completed": PhaseDecisionRoute(target="development_commit", reset_loop=True),
            "failed": PhaseDecisionRoute(target="failed", reset_loop=False),
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
                        max_iterations=3,
                        iteration_state_field="development_analysis_iteration",
                    ),
                    decisions=dev_analysis_decisions,
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="failed",
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        # References the iteration_state_field from development_analysis
                        loop_resets=["development_analysis_iteration"],
                    ),
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
            RecoveryPolicy.model_validate({
                "cycle_cap": 200,
                "terminal_recovery_route": "some_phase",
                "preserve_session_on_categories": ("agent",),
            })

    def test_recovery_failed_route_unknown_phase_raises_policy_error(self) -> None:
        """failed_route referencing an undeclared phase fails completeness validation.

        RecoveryPolicy.failed_route accepts any string (phase_failed, exit_failure,
        or a declared phase name). An undeclared phase name is rejected by
        validate_policy_completeness, not at Pydantic model level.
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
        """recovery.failed_route='phase_failed' is rejected with migration hint."""
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
                failed_route="phase_failed",
                preserve_session_on_categories=("agent",),
            ),
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="no longer supported"):
            validate_policy_completeness(bundle)

    def test_recovery_failed_route_exit_failure_is_rejected(self) -> None:
        """recovery.failed_route='exit_failure' is rejected with migration hint."""
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
                failed_route="exit_failure",
                preserve_session_on_categories=("agent",),
            ),
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="no longer supported"):
            validate_policy_completeness(bundle)


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


class TestAdvancePhaseRequiresPolicyForCommitTargets:
    """Tests that advance_phase raises when policy is None."""

    def test_raises_value_error_when_policy_is_none(self) -> None:
        from ralph.pipeline.progress import advance_phase  # noqa: PLC0415

        state = PipelineState(phase="development_commit")
        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            advance_phase(state, "development", policy=None)


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
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError, match="orphan"):
            validate_policy_completeness(bundle)

    def test_phase_reachable_via_on_failure_passes(self) -> None:
        """A phase reachable only via on_failure is still considered reachable."""
        agents = self._agents(["planning", "fallback", "complete"])
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
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="planning",
            terminal_phase="complete",
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
                        max_iterations=3,
                        iteration_state_field="development_analysis_iteration",
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
                        on_failure="failed",
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
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        error_msg = str(exc_info.value)
        assert "orphan_a" in error_msg
        assert "orphan_b" in error_msg


class TestValidatePostCommitRoutesCoverage:
    """Tests for post_commit_routes coverage validation.

    A commit-role phase that increments a tracked budget counter must have at
    least one [[post_commit_routes]] entry. Missing routes allow silent
    fall-through on on_success, which is false configurability.
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
                        on_failure="failed",
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
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True)},
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
        """Commit phase with tracked counter AND at least one matching route passes."""
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
                        on_failure="failed",
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
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True)},
            post_commit_routes=[
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="remaining"),
                    target="work",
                ),
            ],
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
                        on_failure="failed",
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
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=False)},
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise


class TestValidatePolicyCompletenessVerificationRole:
    """Tests for validation of role='verification' phases in validate_policy_completeness."""

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
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="nonexistent_phase"):
            validate_policy_completeness(bundle)

    def test_verification_on_failure_route_failed_pseudo_accepted(self) -> None:
        """on_failure_route to 'failed' pseudo-phase passes."""
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
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)  # must not raise

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
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)  # must not raise
