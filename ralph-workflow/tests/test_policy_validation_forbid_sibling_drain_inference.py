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

import pytest

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
    PolicyValidationError,
    validate_drain_contracts,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


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
