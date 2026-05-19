"""Tests for ralph/phases/review.py — review phase handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path


from ralph.phases import PhaseContext
from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import AnalysisDecisionEvent, PhaseFailureEvent, PipelineEvent
from ralph.workspace.fs import FsWorkspace


def _fs_context(root: Path) -> PhaseContext:
    workspace = FsWorkspace(root)
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )


class TestHandleReviewAnalysis:
    def _make_context(self) -> MagicMock:
        ctx = MagicMock()
        phase_def = MagicMock()
        phase_def.decisions = {"completed": MagicMock(), "failed": MagicMock()}
        pipeline = MagicMock()
        pipeline.phases = {"review_analysis": phase_def}
        ctx.pipeline_policy = pipeline
        return ctx

    def _mock_invoke_effect(self) -> MagicMock:
        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review_analysis"
        effect.drain = None
        return effect

    def test_proceed_decision_returns_analysis_decision_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "completed"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], AnalysisDecisionEvent)
        assert result[0].phase == "review_analysis"
        assert result[0].decision == "completed"

    def test_unknown_status_returns_phase_failure(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "unknown"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "review_analysis"
        assert event.recoverable is True

    def test_revise_decision_unroutable_returns_phase_failure(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "revise"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "review_analysis"
        assert event.recoverable is True

    def test_failure_decision_returns_analysis_decision_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "failed"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], AnalysisDecisionEvent)
        assert result[0].phase == "review_analysis"
        assert result[0].decision == "failed"

    def test_escalate_decision_unroutable_returns_phase_failure(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "escalate"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "review_analysis"
        assert event.recoverable is True

    def test_missing_artifact_returns_phase_failure_recoverable(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "review_analysis"
        assert event.recoverable is True
        assert "review_analysis_decision" in event.reason

    def test_prepare_prompt_effect_returns_prompt_prepared(self) -> None:
        effect = MagicMock(spec=PreparePromptEffect)
        ctx = self._make_context()

        result = handle_generic_analysis_phase(effect, ctx)
        assert result == [PipelineEvent.PROMPT_PREPARED]
