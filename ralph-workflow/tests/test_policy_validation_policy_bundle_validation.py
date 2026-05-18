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

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


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
