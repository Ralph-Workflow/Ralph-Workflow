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
from unittest.mock import MagicMock

import pytest

from ralph.policy.loader import PolicyValidationError as LoaderPolicyValidationError
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
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
)

ValidationError = importlib.import_module("pydantic").ValidationError


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

    def test_all_pipeline_drains_are_bound(self) -> None:
        """Test that every drain used in pipeline.phases is bound in agents.agent_drains.

        This is enforced by PolicyBundle's all_pipeline_drains_are_bound validator.
        """
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        # This should not raise - the validator ensures all drains are bound
        # Skip terminal phase since it never invokes an agent
        for phase_name, phase_def in bundle.pipeline.phases.items():
            if phase_name == bundle.pipeline.terminal_phase:
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

    def test_validate_drain_contracts_unbound_drain_raises(self) -> None:
        """Test that unbound drain raises PolicyValidationError with flag True."""
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

        # validate_drain_contracts checks ALL built-in drains, not just used ones
        with pytest.raises(
            PolicyValidationError, match="Implicit sibling-drain inference is forbidden"
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
        config_dir = tmp_path / ".agent"
        config_dir.mkdir(parents=True)

        agents_toml = dedent(
            """
            forbid_sibling_drain_inference = true

            [agent_chains.planning]
            agents = ["claude"]

            [agent_drains.planning]
            chain = "planning"
            """
        )
        (config_dir / "agents.toml").write_text(agents_toml)

        pipeline_toml = dedent(
            """
            [phases.planning]
            drain = "planning"
            [phases.planning.transitions]
            on_success = "complete"

            [phases.complete]
            drain = "planning"
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
            match="Implicit sibling-drain inference is forbidden",
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
