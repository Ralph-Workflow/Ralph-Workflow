"""Tests for the development execution phase handled by handle_execution_phase."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.pipeline.effects import Effect, InvokeAgentEffect
from ralph.pipeline.events import AnalysisDecisionEvent, PhaseFailureEvent
from ralph.policy.loader import load_policy


def _decision_markdown(status: str) -> str:
    feedback = (
        "## What Came Up Short\n- [W-1] A defect remains.\n"
        "## How To Fix\n- [FIX-1] Repair the defect.\n"
        if status == "failed"
        else ""
    )
    return (
        "---\ntype: development_analysis_decision\n"
        f"status: {status}\n---\n"
        "## Summary\n- [SUM-1] Analysis complete.\n"
        f"{feedback}"
    )


@lru_cache(maxsize=1)
def _default_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


class TestHandleDevelopmentAnalysis:
    def _default_pipeline_policy(self) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            return load_policy(Path(tmp) / ".agent").pipeline

    def _make_context(self) -> MagicMock:
        ctx = MagicMock()
        ctx.pipeline_policy = self._default_pipeline_policy()
        return ctx

    def _mock_invoke_effect(self) -> MagicMock:
        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "development_analysis"
        effect.drain = None
        return effect

    def test_proceed_decision_returns_analysis_decision_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = _decision_markdown("completed")
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], AnalysisDecisionEvent)
        assert result[0].decision == "completed"

    def test_unknown_status_fails_closed(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = _decision_markdown("unknown")
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], PhaseFailureEvent)
        assert result[0].retry_in_session is True

    def test_revise_status_fails_closed(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = _decision_markdown("revise")
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], PhaseFailureEvent)
        assert result[0].retry_in_session is True

    def test_failure_decision_returns_analysis_decision_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = _decision_markdown("failed")
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], AnalysisDecisionEvent)
        assert result[0].decision == "failed"

    def test_escalate_status_fails_closed(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = _decision_markdown("escalate")
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], PhaseFailureEvent)
        assert result[0].retry_in_session is True

    def test_missing_artifact_returns_phase_failure_recoverable(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development_analysis"
        assert event.recoverable is True
        assert "development_analysis_decision" in event.reason

    def test_non_invoke_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=Effect)
        ctx = self._make_context()

        result = handle_generic_analysis_phase(effect, ctx)
        assert result == []
