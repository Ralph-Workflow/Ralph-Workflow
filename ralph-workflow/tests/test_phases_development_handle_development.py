"""Tests for the development execution phase handled by handle_execution_phase."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from rich.console import Console

from ralph.phases import PhaseContext
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.policy.loader import load_policy

_VALID_PLAN_MARKDOWN = """---
type: plan
schema_version: 1
---
## Summary
Test plan.

Intent: Implement A.
Coverage: feature
## Scope
- [SC-1] Implement A
  Category: feature
- [SC-2] Prove A
  Category: test
- [SC-3] Verify A
  Category: test
## Skills MCP
Skills: test-driven-development
## Steps
### [S-1] Implement A
Implement the change.

Type: file_change
Files:
- modify src/a.py
## Critical Files
- [CF-1] src/a.py
  Action: modify
  Changes: implement A
## Risks
- [R-1] Regression
  Severity: medium
  Mitigation: Run tests.
## Verification
- [V-1] pytest -q
  Expect: tests pass
"""

_VALID_DEV_RESULT_MARKDOWN = """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Done.
## Files Changed
- [F-1] src/a.py
## Plan Items Proven
- [S-1] Implemented.
## Analysis Items Addressed
"""


@lru_cache(maxsize=1)
def _default_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


class TestHandleDevelopment:
    @classmethod
    def _make_context(cls, workspace: object = None, console: object = None) -> PhaseContext:
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
        workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
        workspace.read.return_value = "---\ntype: plan\n---\n## Summary\nInvalid."
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
                ".agent/artifacts/plan.md",
                ".agent/artifacts/development_result.md",
            }
        )
        workspace.read.side_effect = lambda path: (
            _VALID_DEV_RESULT_MARKDOWN
            if path == ".agent/artifacts/development_result.md"
            else _VALID_PLAN_MARKDOWN
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
                ".agent/artifacts/plan.md",
                ".agent/artifacts/development_result.md",
            }
        )
        workspace.read.side_effect = lambda path: (
            _VALID_DEV_RESULT_MARKDOWN
            if path == ".agent/artifacts/development_result.md"
            else _VALID_PLAN_MARKDOWN
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
        workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
        workspace.read.return_value = _VALID_PLAN_MARKDOWN
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
                ".agent/artifacts/plan.md",
                ".agent/artifacts/development_result.md",
            }
        )
        workspace.read.side_effect = lambda path: (
            "---\ntype: wrong_type\n---\n## Summary\nInvalid."
            if path == ".agent/artifacts/development_result.md"
            else _VALID_PLAN_MARKDOWN
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
