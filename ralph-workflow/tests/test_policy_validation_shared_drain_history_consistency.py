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
    ArtifactHistoryPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
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
