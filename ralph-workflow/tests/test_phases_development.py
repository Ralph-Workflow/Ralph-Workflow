"""Tests for the development execution phase handled by handle_execution_phase."""

from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from rich.console import Console

from ralph.phases import PhaseContext
from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import AnalysisDecisionEvent, PhaseFailureEvent, PipelineEvent
from ralph.policy.loader import load_policy

_VALID_PLAN_JSON = json.dumps(
    {"work_units": [{"unit_id": "u1", "description": "A", "allowed_directories": ["src"]}]}
)

_VALID_DEV_RESULT_JSON = json.dumps(
    {
        "type": "development_result",
        "content": {
            "status": "completed",
            "summary": "Done.",
            "files_changed": "- src/a.py",
            "plan_items_proven": [{"plan_item": "u1", "proof": "Implemented."}],
            "analysis_items_addressed": [],
        },
    }
)


@lru_cache(maxsize=1)
def _default_policy_bundle() -> Any:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


class TestHandleDevelopment:
    @classmethod
    def _make_context(cls, workspace=None, console=None) -> PhaseContext:
        policy = _default_policy_bundle()
        ws = workspace if workspace is not None else MagicMock()
        registry: Any = object()
        chain_manager: Any = object()
        agents_policy: Any = object()
        return PhaseContext.construct(
            workspace=ws,
            registry=registry,
            chain_manager=chain_manager,
            pipeline_policy=policy.pipeline,
            artifacts_policy=policy.artifacts,
            agents_policy=agents_policy,
            console=console,
        )

    def test_prepare_prompt_effect_returns_prompt_prepared(self) -> None:
        effect = PreparePromptEffect(phase="development", iteration=1)
        ctx = self._make_context()

        result = handle_execution_phase(effect, ctx)
        assert result == [PipelineEvent.PROMPT_PREPARED]

    def test_invoke_agent_effect_without_plan_artifact_returns_phase_failure_recoverable(
        self,
    ) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = False
        ctx = self._make_context(workspace)

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        result = handle_execution_phase(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert event.recoverable is True
        assert "planning artifact" in event.reason

    def test_invoke_agent_effect_with_invalid_work_units_returns_phase_failure_recoverable(
        self,
    ) -> None:
        workspace = MagicMock()
        workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
        workspace.read.return_value = (
            '{"work_units":[{"unit_id":"u1","description":"A","allowed_directories":["src"],'
            '"dependencies":["missing"]}]}'
        )
        ctx = self._make_context(workspace)

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        result = handle_execution_phase(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert event.recoverable is True

    def test_invoke_agent_effect_with_valid_work_units_returns_agent_success(self) -> None:
        workspace = MagicMock()
        workspace.exists.side_effect = lambda path: (
            path
            in {
                ".agent/artifacts/plan.json",
                ".agent/artifacts/development_result.json",
            }
        )
        workspace.read.side_effect = lambda path: (
            _VALID_DEV_RESULT_JSON
            if path == ".agent/artifacts/development_result.json"
            else _VALID_PLAN_JSON
        )
        ctx = self._make_context(workspace)

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        result = handle_execution_phase(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_invoke_agent_effect_succeeds_even_when_console_is_present(self) -> None:
        workspace = MagicMock()
        workspace.exists.side_effect = lambda path: (
            path
            in {
                ".agent/artifacts/plan.json",
                ".agent/artifacts/development_result.json",
            }
        )
        workspace.read.side_effect = lambda path: (
            _VALID_DEV_RESULT_JSON
            if path == ".agent/artifacts/development_result.json"
            else _VALID_PLAN_JSON
        )
        console = Console(file=StringIO(), force_terminal=True, color_system=None, width=120)
        ctx = self._make_context(workspace, console=console)

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        result = handle_execution_phase(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_invoke_agent_effect_without_development_result_returns_phase_failure(
        self,
    ) -> None:
        workspace = MagicMock()
        workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
        workspace.read.return_value = _VALID_PLAN_JSON
        ctx = self._make_context(workspace)

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        result = handle_execution_phase(effect, ctx)
        failure_events = [event for event in result if isinstance(event, PhaseFailureEvent)]
        assert failure_events, "Missing required development_result must produce PhaseFailureEvent"
        assert failure_events[0].recoverable is True

    def test_invoke_agent_effect_with_malformed_development_result_returns_phase_failure(
        self,
    ) -> None:
        workspace = MagicMock()
        workspace.exists.side_effect = lambda path: (
            path
            in {
                ".agent/artifacts/plan.json",
                ".agent/artifacts/development_result.json",
            }
        )
        workspace.read.side_effect = lambda path: (
            '{"type": "wrong_type", "content": {}}'
            if path == ".agent/artifacts/development_result.json"
            else _VALID_PLAN_JSON
        )
        ctx = self._make_context(workspace)

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        result = handle_execution_phase(effect, ctx)
        # Present optional artifact with wrong type still fails
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert event.recoverable is True

    def test_other_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=Effect)
        ctx = self._make_context()

        result = handle_execution_phase(effect, ctx)
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
        effect.phase = "development_analysis"
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
        assert result[0].decision == "completed"

    def test_unknown_status_returns_phase_failure(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "unknown"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], PhaseFailureEvent)
        assert result[0].recoverable is True

    def test_revise_decision_returns_phase_failure(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "revise"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], PhaseFailureEvent)
        assert result[0].recoverable is True

    def test_failure_decision_returns_analysis_decision_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "failed"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], AnalysisDecisionEvent)
        assert result[0].decision == "failed"

    def test_escalate_decision_returns_phase_failure(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "escalate"}'
        ctx.artifacts_policy.artifacts = {}

        result = handle_generic_analysis_phase(effect, ctx)
        assert len(result) == 1
        assert isinstance(result[0], PhaseFailureEvent)
        assert result[0].recoverable is True

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
