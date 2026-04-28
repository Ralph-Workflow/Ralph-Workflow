"""Tests for ralph/phases/development.py — development phase handler."""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console

from ralph.phases.development import (
    handle_development,
    handle_development_analysis,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.policy.loader import load_policy

_VALID_PLAN_JSON = json.dumps({
    "work_units": [
        {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]}
    ]
})

_VALID_DEV_RESULT_JSON = json.dumps({
    "type": "development_result",
    "content": {
        "status": "completed",
        "summary": "Done.",
        "files_changed": "- src/a.py",
    },
})


class TestHandleDevelopment:
    def _make_context(self) -> MagicMock:
        return MagicMock()

    def test_prepare_prompt_effect_returns_prompt_prepared(self) -> None:
        effect = MagicMock(spec=PreparePromptEffect)
        effect.iteration = 1
        ctx = self._make_context()

        result = handle_development(effect, ctx)
        assert result == [PipelineEvent.PROMPT_PREPARED]

    def test_invoke_agent_effect_without_plan_artifact_returns_phase_failure_recoverable(
        self,
    ) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_development(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert event.recoverable is True
        assert "planning artifact" in event.reason

    def test_invoke_agent_effect_with_invalid_work_units_returns_phase_failure_recoverable(
        self,
    ) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
        ctx.workspace.read.return_value = (
            '{"work_units":[{"unit_id":"u1","description":"A","allowed_directories":["src"],'
            '"dependencies":["missing"]}]}'
        )
        ctx.pipeline_policy.parallel_execution = None

        result = handle_development(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert event.recoverable is True

    def test_invoke_agent_effect_with_valid_work_units_returns_agent_success(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.side_effect = lambda path: path in {
            ".agent/artifacts/plan.json",
            ".agent/artifacts/development_result.json",
        }
        ctx.workspace.read.side_effect = lambda path: (
            _VALID_DEV_RESULT_JSON
            if path == ".agent/artifacts/development_result.json"
            else _VALID_PLAN_JSON
        )

        parallel_execution = MagicMock()
        parallel_execution.max_parallel_workers = 8
        parallel_execution.require_allowed_directories = True
        ctx.pipeline_policy.parallel_execution = parallel_execution

        result = handle_development(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_invoke_agent_effect_succeeds_even_when_console_is_present(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.side_effect = lambda path: path in {
            ".agent/artifacts/plan.json",
            ".agent/artifacts/development_result.json",
        }
        ctx.workspace.read.side_effect = lambda path: (
            _VALID_DEV_RESULT_JSON
            if path == ".agent/artifacts/development_result.json"
            else _VALID_PLAN_JSON
        )
        ctx.console = Console(file=StringIO(), force_terminal=True, color_system=None, width=120)

        parallel_execution = MagicMock()
        parallel_execution.max_parallel_workers = 8
        parallel_execution.require_allowed_directories = True
        ctx.pipeline_policy.parallel_execution = parallel_execution

        result = handle_development(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_invoke_agent_effect_without_development_result_returns_phase_failure(
        self,
    ) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
        ctx.workspace.read.return_value = _VALID_PLAN_JSON

        parallel_execution = MagicMock()
        parallel_execution.max_parallel_workers = 8
        parallel_execution.require_allowed_directories = True
        ctx.pipeline_policy.parallel_execution = parallel_execution

        result = handle_development(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert event.recoverable is True
        assert "development_result" in event.reason

    def test_other_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=Effect)
        ctx = self._make_context()

        result = handle_development(effect, ctx)
        assert result == []


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
        return effect

    def test_proceed_decision_returns_analysis_success(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "completed"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_SUCCESS]

    def test_unknown_status_returns_analysis_loopback(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "unknown"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_LOOPBACK]

    def test_revise_decision_returns_analysis_loopback(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "revise"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_LOOPBACK]

    def test_failure_decision_returns_analysis_loopback(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "failed"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_LOOPBACK]

    def test_escalate_decision_returns_analysis_loopback(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "escalate"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_LOOPBACK]

    def test_missing_artifact_returns_phase_failure_recoverable(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_development_analysis(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development_analysis"
        assert event.recoverable is True
        assert "development_analysis_decision" in event.reason

    def test_non_invoke_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=PreparePromptEffect)
        ctx = self._make_context()

        result = handle_development_analysis(effect, ctx)
        assert result == []
