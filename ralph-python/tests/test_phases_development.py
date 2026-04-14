"""Tests for ralph/phases/development.py — development phase handler."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.phases.development import (
    handle_development,
    handle_development_analysis,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent


class TestHandleDevelopment:
    def _make_context(self) -> MagicMock:
        return MagicMock()

    def test_prepare_prompt_effect_returns_prompt_prepared(self) -> None:
        effect = MagicMock(spec=PreparePromptEffect)
        effect.iteration = 1
        ctx = self._make_context()

        result = handle_development(effect, ctx)
        assert result == [PipelineEvent.PROMPT_PREPARED]

    def test_invoke_agent_effect_returns_agent_success(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_development(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_invoke_agent_effect_with_invalid_work_units_returns_failed(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = (
            '{"work_units":[{"unit_id":"u1","description":"A","allowed_directories":["src"],'
            '"dependencies":["missing"]}]}'
        )
        ctx.pipeline_policy.parallel_execution = None

        result = handle_development(effect, ctx)
        assert result == [PipelineEvent.FAILED]

    def test_invoke_agent_effect_with_valid_work_units_returns_agent_success(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = (
            '{"work_units":[{"unit_id":"u1","description":"A","allowed_directories":["src"]}]}'
        )

        parallel_execution = MagicMock()
        parallel_execution.max_parallel_workers = 8
        parallel_execution.require_allowed_directories = True
        ctx.pipeline_policy.parallel_execution = parallel_execution

        result = handle_development(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_other_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=Effect)
        ctx = self._make_context()

        result = handle_development(effect, ctx)
        assert result == []


class TestHandleDevelopmentAnalysis:
    def _make_context(self) -> MagicMock:
        return MagicMock()

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

    def test_complete_decision_returns_analysis_success(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "unknown"}'  # maps to COMPLETE

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_SUCCESS]

    def test_revise_decision_returns_analysis_loopback(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "revise"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_LOOPBACK]

    def test_failure_decision_returns_failed_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "failed"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.FAILED]

    def test_escalate_decision_returns_failed_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "escalate"}'

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.FAILED]

    def test_missing_artifact_defaults_to_proceed(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_development_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_SUCCESS]

    def test_non_invoke_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=PreparePromptEffect)
        ctx = self._make_context()

        result = handle_development_analysis(effect, ctx)
        assert result == []
