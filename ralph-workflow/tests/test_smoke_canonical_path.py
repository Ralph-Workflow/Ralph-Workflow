"""Smoke plumbing end-to-end canonical receipt tests.

Tests both the Claude branch (through handle_submit_artifact) and the AGY
branch (through direct file write + promotion) to ensure the canonical receipt
path is stamped in both cases.
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import is_artifact_submitted
from ralph.agents.invoke import InvokeOptions
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.smoke_test_result import SMOKE_TEST_RESULT_ARTIFACT_TYPE
from ralph.mcp.tools.artifact import handle_submit_artifact
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing.smoke_plumbing import SmokeRunParams, _run_smoke_agent
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

pytestmark = pytest.mark.smoke

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def _smoke_payload() -> dict[str, object]:
    return {
        "status": "passed",
        "output_file": "tmp/interactive-claude-smoke/todo-list.js",
        "observed_working": ["created todo-list.js"],
        "observed_breaks": [],
        "headless_guide_checks": ["tool activity"],
        "summary": "Smoke test passed",
    }


def _outer_envelope() -> dict[str, object]:
    return {
        "name": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
        "type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
        "content": _smoke_payload(),
    }


def _claude_config() -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        can_commit=False,
        json_parser="claude",
        transport=AgentTransport.CLAUDE,
    )


def _agy_config() -> AgentConfig:
    return AgentConfig(
        cmd="agy",
        can_commit=False,
        json_parser="generic",
        transport=AgentTransport.AGY,
    )


class _SmokeSession:
    session_id = "sess-smoke"
    run_id = "interactive-claude-smoke"
    drain = "development"

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


class _StubBridge:
    run_id = "interactive-claude-smoke"

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:65535/mcp"

    def shutdown(self) -> None:
        return None


def _make_params(
    tmp_path: Path,
    agent_name: str,
    config: AgentConfig,
) -> SmokeRunParams:
    relative_dir = (
        tmp_path / "tmp" / "interactive-claude-smoke"
        if config.transport == AgentTransport.CLAUDE
        else tmp_path / "tmp" / "interactive-agy-smoke"
    )
    relative_dir.mkdir(parents=True, exist_ok=True)
    output_file = relative_dir / "todo-list.js"
    output_file.write_text("// smoke output", encoding="utf-8")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    return SmokeRunParams(
        agent_name=agent_name,
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=_StubBridge(),
        pipeline_deps=object(),
    )


def test_smoke_claude_branch_end_to_end_uses_canonical_submit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Claude branch: agent calls handle_submit_artifact, receipt is stamped."""
    workspace = MockWorkspace(tmp_path)
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append('{"type":"session","session_id":"sess-smoke"}')
            raw_sink.append('{"type":"tool_use","tool":"submit_artifact"}')
            raw_sink.append('{"type":"tool_result","tool":"submit_artifact"}')
            raw_sink.append("Task declared complete: smoke done")
        rendered_sink = kwargs.get("rendered_output_sink")
        if isinstance(rendered_sink, deque):
            rendered_sink.append("tool_use: submit_artifact")
            rendered_sink.append("tool_result: submit_artifact")
        handle_submit_artifact(
            _SmokeSession(),
            workspace,
            {
                "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
                "content": json.dumps(_smoke_payload()),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted is True
    assert artifact_receipt_present(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)
    assert is_artifact_submitted(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)


def test_smoke_agy_fallback_end_to_end_uses_canonical_submit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """AGY branch: direct file write is promoted to canonical receipt."""
    params = _make_params(tmp_path, "agy/test-model", _agy_config())
    run_id = "interactive-agy-smoke-test-model"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        artifact_type = SMOKE_TEST_RESULT_ARTIFACT_TYPE
        artifact_path = tmp_path / ".agent" / "artifacts" / f"{artifact_type}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(_outer_envelope()), encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted is True
    assert is_artifact_submitted(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)
    assert artifact_receipt_present(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)
