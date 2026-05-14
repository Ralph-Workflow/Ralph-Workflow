"""Regression harness for bounded runner agent-output retention."""

from __future__ import annotations

import json
import tracemalloc
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


_AGENT_OUTPUT_LINES = 32
_AGENT_OUTPUT_BYTES = 4_096
_WARMUP_CALLS = 1
_SAMPLE_CALLS = 8
_CURRENT_BUDGET_BYTES = 512_000
_PEAK_BUDGET_BYTES = 4 * 1024 * 1024


class _FakeBridge:
    def shutdown(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:9999/mcp"


class _FakeRegistry:
    def __init__(self) -> None:
        self._agent_config = AgentConfig(cmd="claude", output_flag="--json-stream")

    def get(self, name: str) -> AgentConfig | None:
        del name
        return self._agent_config

    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _FakeRegistry:
        del config
        return cls()


def _make_config() -> UnifiedConfig:
    return UnifiedConfig(general=GeneralConfig(max_same_agent_retries=0))


def _fake_invoke_agent() -> Iterator[str]:
    payload = "x" * _AGENT_OUTPUT_BYTES
    yield json.dumps({"session_id": "sess-development"})
    for idx in range(1, _AGENT_OUTPUT_LINES):
        yield f"development:{idx}:{payload}"
    raise AgentInvocationError("claude", 1, "stderr exploded")


def _install_runner_seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "start_mcp_server", lambda *_args, **_kwargs: _FakeBridge())
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
    monkeypatch.setattr(
        runner_module,
        "materialize_system_prompt",
        lambda **_kwargs: str(tmp_path / "SYSTEM_PROMPT.md"),
    )
    monkeypatch.setattr(runner_module, "check_mcp_bridge_health", lambda _bridge: None)


def _run_once(tmp_path: Path) -> PipelineEvent:
    effect = InvokeAgentEffect(agent_name="claude", phase="development", prompt_file="PROMPT.md")
    result = runner_module._execute_agent_effect(
        effect,
        _make_config(),
        runner_module._AgentExecutionDeps(
            invoke_agent=lambda *_args, **_kwargs: _fake_invoke_agent(),
            agent_invocation_error=AgentInvocationError,
            agent_registry=_FakeRegistry,
        ),
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        verbosity=Verbosity.QUIET,
    )
    return result


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_run_pipeline_memory_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_runner_seams(monkeypatch, tmp_path)

    for _ in range(_WARMUP_CALLS):
        assert _run_once(tmp_path) == PipelineEvent.AGENT_FAILURE

    current_samples: list[int] = []
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        for _ in range(_SAMPLE_CALLS):
            assert _run_once(tmp_path) == PipelineEvent.AGENT_FAILURE
            current, peak = tracemalloc.get_traced_memory()
            current_samples.append(current)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert max(current_samples) - min(current_samples) < _CURRENT_BUDGET_BYTES
    assert peak < _PEAK_BUDGET_BYTES
