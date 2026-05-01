"""Regression tests: _render_phase_artifact_handoff uses role/artifact-type dispatch.

Verifies that artifact handoff rendering is fully driven by policy role and artifact
contracts, with no hardcoded canonical phase names. Uses a custom-named PolicyBundle
(design/build/audit phases) to confirm generic dispatch works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.runner import _render_phase_artifact_handoff
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)


def _make_custom_bundle() -> PolicyBundle:
    """Return a PolicyBundle with fully custom phase names: design/build/audit/done/aborted."""
    agents = AgentsPolicy(
        agent_chains={
            "design_chain": AgentChainConfig(agents=["fake-agent"]),
            "build_chain": AgentChainConfig(agents=["fake-agent"]),
            "audit_chain": AgentChainConfig(agents=["fake-agent"]),
        },
        agent_drains={
            "design": AgentDrainConfig(chain="design_chain", drain_class="planning"),
            "build": AgentDrainConfig(chain="build_chain", drain_class="development"),
            "audit": AgentDrainConfig(chain="audit_chain", drain_class="analysis"),
            "done": AgentDrainConfig(chain="design_chain"),
            "aborted": AgentDrainConfig(chain="design_chain"),
        },
    )

    pipeline = PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        phases={
            "design": PhaseDefinition(
                drain="design",
                role="execution",
                transitions=PhaseTransition(on_success="build"),
            ),
            "build": PhaseDefinition(
                drain="build",
                role="execution",
                transitions=PhaseTransition(on_success="audit"),
            ),
            "audit": PhaseDefinition(
                drain="audit",
                role="analysis",
                transitions=PhaseTransition(on_success="done", on_loopback="build"),
                decisions={},
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
            "aborted": PhaseDefinition(
                drain="aborted",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="aborted", on_loopback="aborted"),
            ),
        },
        recovery=RecoveryPolicy(failed_route="aborted"),
    )

    artifacts = ArtifactsPolicy(
        artifacts={
            "audit_decision": ArtifactContract(
                drain="audit",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["completed", "request_changes"],
            ),
        }
    )

    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)


@pytest.fixture
def custom_bundle() -> PolicyBundle:
    return _make_custom_bundle()


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestRenderPhaseArtifactHandoffIsGeneric:
    """_render_phase_artifact_handoff dispatches by role, not by phase name."""

    def test_analysis_phase_with_analysis_decision_contract_calls_render_analysis_decision(
        self,
        custom_bundle: PolicyBundle,
        tmp_workspace: Path,
    ) -> None:
        """Audit phase (analysis role) with contract calls render_analysis_decision."""
        ctx = MagicMock()
        with patch(
            "ralph.pipeline.runner.render_analysis_decision"
        ) as mock_render:
            _render_phase_artifact_handoff(
                "audit",
                PipelineEvent.AGENT_SUCCESS,
                tmp_workspace,
                None,
                display_context=ctx,
                drain="audit",
                policy_bundle=custom_bundle,
            )
        mock_render.assert_called_once_with(tmp_workspace, "audit", ctx)

    def test_execution_role_phase_without_contract_skips_all_renderers(
        self,
        custom_bundle: PolicyBundle,
        tmp_workspace: Path,
    ) -> None:
        """Design phase (execution role) has no artifact contract — skips rendering."""
        ctx = MagicMock()
        with (
            patch("ralph.pipeline.runner.render_plan_artifact") as mock_plan,
            patch("ralph.pipeline.runner.render_analysis_decision") as mock_analysis,
            patch("ralph.pipeline.runner.render_development_artifact") as mock_dev,
        ):
            _render_phase_artifact_handoff(
                "design",
                PipelineEvent.AGENT_SUCCESS,
                tmp_workspace,
                None,
                display_context=ctx,
                drain="design",
                policy_bundle=custom_bundle,
            )
        mock_plan.assert_not_called()
        mock_analysis.assert_not_called()
        mock_dev.assert_not_called()

    def test_build_phase_without_contract_skips_all_renderers(
        self,
        custom_bundle: PolicyBundle,
        tmp_workspace: Path,
    ) -> None:
        """Build phase (execution role) has no artifact contract — skips rendering."""
        ctx = MagicMock()
        with (
            patch("ralph.pipeline.runner.render_plan_artifact") as mock_plan,
            patch("ralph.pipeline.runner.render_analysis_decision") as mock_analysis,
            patch("ralph.pipeline.runner.render_development_artifact") as mock_dev,
        ):
            _render_phase_artifact_handoff(
                "build",
                PipelineEvent.AGENT_SUCCESS,
                tmp_workspace,
                None,
                display_context=ctx,
                drain="build",
                policy_bundle=custom_bundle,
            )
        mock_plan.assert_not_called()
        mock_analysis.assert_not_called()
        mock_dev.assert_not_called()

    def test_non_agent_success_event_skips_render_for_no_contract_phase(
        self,
        custom_bundle: PolicyBundle,
        tmp_workspace: Path,
    ) -> None:
        """Non-AGENT_SUCCESS events skip rendering for phases without an artifact contract."""
        ctx = MagicMock()
        with patch("ralph.pipeline.runner.render_plan_artifact") as mock_render:
            _render_phase_artifact_handoff(
                "design",
                PipelineEvent.ANALYSIS_LOOPBACK,
                tmp_workspace,
                None,
                display_context=ctx,
                drain="design",
                policy_bundle=custom_bundle,
            )
        mock_render.assert_not_called()

    def test_no_policy_bundle_skips_all_renderers(
        self,
        tmp_workspace: Path,
    ) -> None:
        """Without a policy_bundle, rendering is skipped for any phase name."""
        ctx = MagicMock()
        with (
            patch("ralph.pipeline.runner.render_plan_artifact") as mock_plan,
            patch("ralph.pipeline.runner.render_analysis_decision") as mock_analysis,
            patch("ralph.pipeline.runner.render_development_artifact") as mock_dev,
        ):
            _render_phase_artifact_handoff(
                "planning",
                PipelineEvent.AGENT_SUCCESS,
                tmp_workspace,
                None,
                display_context=ctx,
                drain="planning",
                policy_bundle=None,
            )
        mock_plan.assert_not_called()
        mock_analysis.assert_not_called()
        mock_dev.assert_not_called()
