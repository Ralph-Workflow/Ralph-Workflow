"""Regression harness for bounded runner agent-output retention."""

from __future__ import annotations

import gc
import json
import tracemalloc
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import JsonParserType, Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest import MonkeyPatch


_LINE_COUNT = 64
_LINE_SIZE = 4096
_ITERATION_COUNT = 8
_RETAINED_DELTA_LIMIT = 2_000_000
_PEAK_DELTA_LIMIT = 6_000_000


@dataclass
class _GeneralConfigStub:
    verbosity: int = 0
    agent_idle_timeout_seconds: float | None = 300.0
    agent_idle_drain_window_seconds: float = 2.0
    agent_idle_max_waiting_on_child_seconds: float = 1800.0
    agent_idle_poll_interval_seconds: float = 0.5
    agent_max_session_seconds: float | None = None
    agent_descendant_wait_timeout_seconds: float = 30.0
    agent_descendant_wait_poll_seconds: float = 0.25
    agent_parent_exit_grace_seconds: float = 5.0
    agent_waiting_status_interval_seconds: float = 60.0
    agent_suspect_waiting_on_child_seconds: float | None = 300.0
    agent_idle_no_progress_waiting_on_child_seconds: float = 600.0
    agent_child_progress_ttl_seconds: float = 300.0
    agent_child_heartbeat_ttl_seconds: float = 60.0
    agent_child_stale_label_ttl_seconds: float = 120.0
    agent_child_exit_reconcile_seconds: float = 5.0
    agent_process_exit_wait_seconds: float = 5.0
    cloud_mode: bool = False
    agent_system_prompt: str | None = None
    agent_provider: str | None = None
    verbose: bool = False


@dataclass
class _CloudConfigStub:
    enabled: bool = False
    api_url: str | None = None
    api_key: str | None = None
    timeout_secs: float = 30.0


@dataclass
class _CcsConfigStub:
    enabled: bool = False


@dataclass
class _ConfigStub:
    general: _GeneralConfigStub = field(default_factory=_GeneralConfigStub)
    cloud: _CloudConfigStub = field(default_factory=_CloudConfigStub)
    ccs: _CcsConfigStub = field(default_factory=_CcsConfigStub)
    ccs_aliases: dict[str, str] = field(default_factory=dict)


class _RegistryInstance:
    def get(self, name: str) -> AgentConfig | None:
        del name
        return AgentConfig(
            cmd="generic-agent",
            output_flag="--json-stream",
            json_parser=JsonParserType.GENERIC,
        )


class _RegistryFactory:
    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _RegistryInstance:
        del cls, config
        return _RegistryInstance()


class _FakeBridge:
    def shutdown(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"


class _NullSupervisor:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
        del exc_type, exc, tb
        return False


def _start_mcp_server(*_args: object, **_kwargs: object) -> _FakeBridge:
    return _FakeBridge()


def _shutdown_mcp_server(_bridge: object) -> None:
    return None


def _check_mcp_bridge_health(_bridge: object) -> None:
    return None


def _mcp_supervisor(*args: object, **kwargs: object) -> _NullSupervisor:
    del args, kwargs
    return _NullSupervisor()


def _materialize_system_prompt(**kwargs: object) -> None:
    del kwargs


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


def _install_runner_seams(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "start_mcp_server", _start_mcp_server)
    monkeypatch.setattr(runner, "shutdown_mcp_server", _shutdown_mcp_server)
    monkeypatch.setattr(runner, "check_mcp_bridge_health", _check_mcp_bridge_health)
    monkeypatch.setattr(runner, "McpSupervisor", _mcp_supervisor)
    monkeypatch.setattr(runner, "materialize_system_prompt", _materialize_system_prompt)
    monkeypatch.setattr(runner, "build_session_mcp_plan", _build_session_mcp_plan)
    monkeypatch.setattr(runner, "_emit_display_line", _emit_display_line)


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
    session_line = json.dumps(session_payload)
    payload = "x" * _LINE_SIZE
    for idx in range(_LINE_COUNT):
        prefix = session_line if idx == 0 else f"development:{idx}:"
        yield f"{prefix}{payload}"


@pytest.mark.integration
def test_run_pipeline_memory_regression(monkeypatch: MonkeyPatch) -> None:
    _install_runner_seams(monkeypatch)

    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development",
        prompt_file="PROMPT.md",
    )
    deps = runner._AgentExecutionDeps(
        invoke_agent=_fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
        agent_registry=_RegistryFactory,
    )
    workspace_scope = WorkspaceScope("/tmp/worktree")
    display_context = make_display_context()

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    for _ in range(_ITERATION_COUNT):
        event = runner._execute_agent_effect(
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
