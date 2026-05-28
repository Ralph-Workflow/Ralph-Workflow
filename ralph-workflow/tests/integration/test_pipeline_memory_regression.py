"""Regression harness for bounded runner agent-output retention."""

from __future__ import annotations

import gc
import json
import tracemalloc
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pytest import MonkeyPatch

    from ralph.config.models import AgentConfig, UnifiedConfig
from tests.integration.test_pipeline_memory_regression_helper__configstub import _ConfigStub
from tests.integration.test_pipeline_memory_regression_helper__fakebridge import _FakeBridge
from tests.integration.test_pipeline_memory_regression_helper__nullsupervisor import _NullSupervisor
from tests.integration.test_pipeline_memory_regression_helper__registryfactory import (
    _RegistryFactory,
)

_LINE_COUNT = 32
_LINE_SIZE = 2048
_ITERATION_COUNT = 8
_RETAINED_DELTA_LIMIT = 2_000_000
_PEAK_DELTA_LIMIT = 6_000_000


def _start_mcp_server(*_args: object, **_kwargs: object) -> _FakeBridge:
    return _FakeBridge()


def _shutdown_mcp_server(_bridge: object) -> None:
    return None


def _check_mcp_bridge_health(_bridge: object) -> None:
    return None


def _mcp_supervisor(*args: object, **kwargs: object) -> _NullSupervisor:
    del args, kwargs
    return _NullSupervisor()


def _build_session_mcp_plan(**kwargs: object) -> SimpleNamespace:
    del kwargs
    return SimpleNamespace(
        capabilities=(),
        server_env=cast("dict[str, str]", {}),
        model_identity=None,
        capability_profile=None,
    )


def _emit_display_line(*args: object, **kwargs: object) -> None:
    del args, kwargs


def _install_runner_seams(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "start_mcp_server", _start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", _shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "check_mcp_bridge_health", _check_mcp_bridge_health)
    monkeypatch.setattr(runner_module, "McpSupervisor", _mcp_supervisor)
    monkeypatch.setattr(
        runner_module,
        "materialize_system_prompt",
        lambda **_kwargs: str(tmp_path / "SYSTEM_PROMPT.md"),
    )
    monkeypatch.setattr(runner_module, "build_session_mcp_plan", _build_session_mcp_plan)
    monkeypatch.setattr(runner_module, "emit_display_line", _emit_display_line)


def _config() -> UnifiedConfig:
    return cast("UnifiedConfig", _ConfigStub())


def _fake_invoke_agent(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: object | None = None,
) -> Iterator[str]:
    del config, prompt_file, options
    session_payload: dict[str, str] = {"session_id": "sess-development"}
    yield json.dumps(session_payload)
    payload = "x" * _LINE_SIZE
    for idx in range(1, _LINE_COUNT):
        yield f"development:{idx}:{payload}"


@pytest.mark.integration
@pytest.mark.timeout_seconds(10)
def test_run_pipeline_memory_regression(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _install_runner_seams(monkeypatch, tmp_path)

    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development",
        prompt_file="PROMPT.md",
    )
    deps = runner_module.AgentExecutionDeps(
        invoke_agent=_fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
        agent_registry=_RegistryFactory,
    )
    workspace_scope = WorkspaceScope(tmp_path)
    display_context = make_display_context()

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    for _ in range(_ITERATION_COUNT):
        event = runner_module.execute_agent_effect(
            effect,
            _config(),
            deps,
            workspace_scope,
            display_context=display_context,
            verbosity=Verbosity.QUIET,
        )
        assert event == PipelineEvent.AGENT_SUCCESS

    gc.collect()
    final_current, peak_current = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    retained_delta_bytes = final_current - baseline_current
    peak_delta_bytes = peak_current - baseline_current

    assert retained_delta_bytes <= _RETAINED_DELTA_LIMIT
    assert peak_delta_bytes <= _PEAK_DELTA_LIMIT
