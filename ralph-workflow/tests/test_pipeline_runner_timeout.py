"""Tests that agent_idle_timeout_seconds from config flows through to InvokeOptions."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.startup import HeartbeatPolicy
from ralph.mcp.server.lifecycle import McpServerError, RestartAwareMcpBridge
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.runner import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _make_config(agent_idle_timeout_seconds: float) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(agent_idle_timeout_seconds=agent_idle_timeout_seconds)
    )


def _registry_factory(config: object) -> object:
    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class RegistryInstance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class Registry:
        @classmethod
        def from_config(cls, cfg: object) -> object:
            del cls, cfg
            return RegistryInstance()

    return Registry


def test_config_idle_timeout_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """UnifiedConfig.general.agent_idle_timeout_seconds is passed to InvokeOptions."""
    custom_timeout = 7.0
    config = _make_config(custom_timeout)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

    def fake_start_mcp_server(*_args: object, **_kwargs: object) -> FakeBridge:
        return FakeBridge()

    def fake_shutdown_mcp_server(_bridge: object) -> None:
        return

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        del workspace_root, name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
    ) -> list[str]:
        del config, prompt_file
        captured["idle_timeout_seconds"] = (
            getattr(options, "idle_timeout_seconds", None) if options else None
        )
        return []

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )

    runner_module._execute_agent_effect(
        effect, config, deps, WorkspaceScope(tmp_path), display_context=make_display_context()
    )

    assert captured.get("idle_timeout_seconds") == custom_timeout


def test_config_default_idle_timeout_is_300() -> None:
    """Default GeneralConfig.agent_idle_timeout_seconds is 300.0 seconds."""
    config = GeneralConfig()
    assert config.agent_idle_timeout_seconds == 300.0


def test_config_idle_timeout_validation_rejects_zero() -> None:
    """agent_idle_timeout_seconds must be greater than zero."""
    with pytest.raises(ValidationError):
        GeneralConfig(agent_idle_timeout_seconds=0.0)


def test_config_idle_timeout_validation_rejects_negative() -> None:
    """agent_idle_timeout_seconds must be greater than zero."""
    with pytest.raises(ValidationError):
        GeneralConfig(agent_idle_timeout_seconds=-1.0)


def _make_config_with_watchdog(
    agent_idle_timeout_seconds: float = 300.0,
    agent_idle_drain_window_seconds: float = 0.5,
    agent_idle_max_waiting_on_child_seconds: float = 1800.0,
) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(
            agent_idle_timeout_seconds=agent_idle_timeout_seconds,
            agent_idle_drain_window_seconds=agent_idle_drain_window_seconds,
            agent_idle_max_waiting_on_child_seconds=agent_idle_max_waiting_on_child_seconds,
        )
    )


def _capture_options_factory(captured: dict[str, object]) -> object:
    """Return a fake_invoke_agent that captures the full options object."""
    from ralph.config.models import AgentConfig as _AgentConfig

    def fake_invoke_agent(
        config: _AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
    ) -> list[str]:
        del config, prompt_file
        captured["options"] = options
        return []

    return fake_invoke_agent


def _run_with_config(
    config: UnifiedConfig,
    effect: InvokeAgentEffect,
    captured: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Helper that wires up mocks and runs _execute_agent_effect."""

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

    def fake_start_mcp_server(*_args: object, **_kwargs: object) -> FakeBridge:
        return FakeBridge()

    def fake_shutdown_mcp_server(_bridge: object) -> None:
        return

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        del workspace_root, name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=_capture_options_factory(captured),
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )
    runner_module._execute_agent_effect(
        effect, config, deps, WorkspaceScope(tmp_path), display_context=make_display_context()
    )


def test_runner_sets_agent_label_scope_to_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _make_config_with_watchdog()
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    extra_env = getattr(options, "extra_env", None)
    assert isinstance(extra_env, dict)
    assert extra_env[str(AGENT_LABEL_SCOPE_ENV)] == extra_env[str(MCP_RUN_ID_ENV)]


def test_config_drain_window_seconds_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_idle_drain_window_seconds is passed to InvokeOptions."""
    custom_drain = 2.5
    config = _make_config_with_watchdog(agent_idle_drain_window_seconds=custom_drain)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "drain_window_seconds", None) == custom_drain


def test_config_max_waiting_seconds_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_idle_max_waiting_on_child_seconds is passed to InvokeOptions."""
    custom_max = 900.0
    config = _make_config_with_watchdog(agent_idle_max_waiting_on_child_seconds=custom_max)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "max_waiting_on_child_seconds", None) == custom_max


def test_config_default_drain_window_is_0_5() -> None:
    """Default GeneralConfig.agent_idle_drain_window_seconds is 0.5s."""
    config = GeneralConfig()
    assert config.agent_idle_drain_window_seconds == 0.5


def test_config_default_max_waiting_is_1800() -> None:
    """Default GeneralConfig.agent_idle_max_waiting_on_child_seconds is 1800.0s."""
    config = GeneralConfig()
    assert config.agent_idle_max_waiting_on_child_seconds == 1800.0


def _make_config_full(
    agent_idle_timeout_seconds: float = 300.0,
    agent_idle_drain_window_seconds: float = 0.5,
    agent_idle_max_waiting_on_child_seconds: float = 1800.0,
    agent_idle_poll_interval_seconds: float = 0.05,
    agent_parent_exit_grace_seconds: float = 5.0,
    agent_descendant_wait_timeout_seconds: float = 30.0,
    agent_descendant_wait_poll_seconds: float = 0.5,
    agent_process_exit_wait_seconds: float = 30.0,
    agent_max_session_seconds: float | None = None,
) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(
            agent_idle_timeout_seconds=agent_idle_timeout_seconds,
            agent_idle_drain_window_seconds=agent_idle_drain_window_seconds,
            agent_idle_max_waiting_on_child_seconds=agent_idle_max_waiting_on_child_seconds,
            agent_idle_poll_interval_seconds=agent_idle_poll_interval_seconds,
            agent_parent_exit_grace_seconds=agent_parent_exit_grace_seconds,
            agent_descendant_wait_timeout_seconds=agent_descendant_wait_timeout_seconds,
            agent_descendant_wait_poll_seconds=agent_descendant_wait_poll_seconds,
            agent_process_exit_wait_seconds=agent_process_exit_wait_seconds,
            agent_max_session_seconds=agent_max_session_seconds,
        )
    )


def test_config_idle_poll_interval_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_idle_poll_interval_seconds is passed to InvokeOptions."""
    custom_poll = 0.1
    config = _make_config_full(agent_idle_poll_interval_seconds=custom_poll)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "idle_poll_interval_seconds", None) == custom_poll


def test_config_parent_exit_grace_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_parent_exit_grace_seconds is passed to InvokeOptions."""
    custom_grace = 10.0
    config = _make_config_full(agent_parent_exit_grace_seconds=custom_grace)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "parent_exit_grace_seconds", None) == custom_grace


def test_config_descendant_wait_timeout_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_descendant_wait_timeout_seconds is passed to InvokeOptions."""
    custom_wait = 60.0
    config = _make_config_full(agent_descendant_wait_timeout_seconds=custom_wait)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "descendant_wait_timeout_seconds", None) == custom_wait


def test_config_descendant_wait_poll_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_descendant_wait_poll_seconds is passed to InvokeOptions."""
    custom_poll = 0.25
    config = _make_config_full(agent_descendant_wait_poll_seconds=custom_poll)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "descendant_wait_poll_seconds", None) == custom_poll


def test_config_process_exit_wait_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_process_exit_wait_seconds is passed to InvokeOptions."""
    custom_exit_wait = 45.0
    config = _make_config_full(agent_process_exit_wait_seconds=custom_exit_wait)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "process_exit_wait_seconds", None) == custom_exit_wait


def test_config_max_session_seconds_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_max_session_seconds is passed to InvokeOptions."""
    custom_session = 3600.0
    config = _make_config_full(agent_max_session_seconds=custom_session)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "max_session_seconds", None) == custom_session


def test_config_default_idle_poll_interval_is_0_05() -> None:
    """Default GeneralConfig.agent_idle_poll_interval_seconds is 0.05s."""
    config = GeneralConfig()
    assert config.agent_idle_poll_interval_seconds == 0.05


def test_config_default_process_exit_wait_is_30() -> None:
    """Default GeneralConfig.agent_process_exit_wait_seconds is 30.0s."""
    config = GeneralConfig()
    assert config.agent_process_exit_wait_seconds == 30.0


def test_config_default_descendant_wait_poll_is_0_5() -> None:
    """Default GeneralConfig.agent_descendant_wait_poll_seconds is 0.5s."""
    config = GeneralConfig()
    assert config.agent_descendant_wait_poll_seconds == 0.5


# ---------------------------------------------------------------------------
# McpServerError / bridge-sharing tests
# ---------------------------------------------------------------------------


def test_mcp_server_error_causes_agent_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """McpServerError from check_mcp_bridge_health surfaces as AGENT_FAILURE."""
    config = _make_config(300.0)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

    def fake_start_mcp_server(*_args: object, **_kwargs: object) -> FakeBridge:
        return FakeBridge()

    def fake_shutdown_mcp_server(_bridge: object) -> None:
        return

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        del workspace_root, name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    def fake_check_mcp_bridge_health(_bridge: object) -> None:
        raise McpServerError("budget exhausted", restart_count=3)

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)
    monkeypatch.setattr(runner_module, "check_mcp_bridge_health", fake_check_mcp_bridge_health)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=lambda *_a, **_kw: [],
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )

    result = runner_module._execute_agent_effect(
        effect, config, deps, WorkspaceScope(tmp_path), display_context=make_display_context()
    )
    assert result == PipelineEvent.AGENT_FAILURE


def test_bridge_shared_across_retry_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """start_mcp_server is called once; the same bridge serves all retry attempts."""
    config = _make_config(300.0)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    bridge_start_calls: list[int] = []
    invoke_calls: list[int] = []
    attempt = 0

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

    def fake_start_mcp_server(*_args: object, **_kwargs: object) -> FakeBridge:
        bridge_start_calls.append(1)
        return FakeBridge()

    def fake_shutdown_mcp_server(_bridge: object) -> None:
        return

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        del workspace_root, name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    def fake_invoke_agent(
        _agent_cfg: AgentConfig, prompt_file: str, *, options: object = None
    ) -> list[str]:
        nonlocal attempt
        invoke_calls.append(attempt)
        attempt += 1
        if attempt < 2:
            raise RuntimeError("timeout")
        return []

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )

    runner_module._execute_agent_effect(
        effect, config, deps, WorkspaceScope(tmp_path), display_context=make_display_context()
    )

    assert len(bridge_start_calls) == 1, "bridge must be started only once"
    assert len(invoke_calls) == 2, "agent must be invoked twice (retry)"


def test_check_mcp_bridge_health_called_per_retry_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """check_mcp_bridge_health is called once per attempt (not just once at start)."""
    config = _make_config(300.0)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    health_check_calls: list[int] = []
    invoke_attempt = 0

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

    def fake_start_mcp_server(*_args: object, **_kwargs: object) -> FakeBridge:
        return FakeBridge()

    def fake_shutdown_mcp_server(_bridge: object) -> None:
        return

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        del workspace_root, name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    def fake_check_mcp_bridge_health(_bridge: object) -> None:
        health_check_calls.append(1)

    def fake_invoke_agent(
        cfg: AgentConfig, prompt_file: str, *, options: object = None
    ) -> list[str]:
        nonlocal invoke_attempt
        invoke_attempt += 1
        if invoke_attempt == 1:
            raise RuntimeError("timeout on attempt 1")
        return []

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)
    monkeypatch.setattr(runner_module, "check_mcp_bridge_health", fake_check_mcp_bridge_health)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )

    runner_module._execute_agent_effect(
        effect, config, deps, WorkspaceScope(tmp_path), display_context=make_display_context()
    )

    assert len(health_check_calls) == 2, (
        f"health check must fire on each attempt; got {len(health_check_calls)} call(s)"
    )


def test_record_mcp_restart_forwarded_to_subscriber(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When bridge.restart_count > 0, record_mcp_restart is called on the display subscriber."""
    import pathlib
    import tempfile

    from ralph.mcp.server.lifecycle import (
        McpRestartPolicy,
        ProcessLike,
        RestartAwareMcpBridge,
        StandaloneMcpProcess,
    )

    config = _make_config(300.0)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    # Build a RestartAwareMcpBridge that already has a restart
    td = pathlib.Path(tempfile.mkdtemp())
    sf = td / "session.json"
    sf.write_text("{}", encoding="utf-8")

    class _FakePopen:
        def poll(self) -> int | None:
            return None

        def terminate(self, grace_period_s: float = 5.0) -> None:
            return

        def wait(self, timeout: object = None) -> int:
            return 0

        def kill(self) -> None:
            return

        @property
        def pid(self) -> int:
            return 1

    inner = StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9888/mcp",
        process=cast("ProcessLike", _FakePopen()),
        session_file=sf,
    )
    bridge = RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=McpRestartPolicy(max_restarts=3),
    )
    # Manually advance restart count to 1 without the restart loop
    bridge._restart_count = 1

    def fake_start_mcp_server(*_args: object, **_kwargs: object) -> RestartAwareMcpBridge:
        return bridge

    def fake_shutdown_mcp_server(_bridge: object) -> None:
        return

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        del workspace_root, name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    recorded: list[int] = []

    class FakeSubscriber:
        def record_mcp_restart(self, restart_count: int) -> None:
            recorded.append(restart_count)

    class _FakeDisplay:
        def __init__(self) -> None:
            self._ctx = make_display_context()
            self.subscriber = FakeSubscriber()

        def emit(self, _unit_id: str, _line: str) -> None:
            return

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=lambda *_a, **_kw: [],
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )

    runner_module._execute_agent_effect(
        effect,
        config,
        deps,
        WorkspaceScope(tmp_path),
        display=cast("runner_module.ParallelDisplay", _FakeDisplay()),
    )

    assert recorded == [1], f"expected record_mcp_restart([1]); got {recorded}"


def test_supervision_interval_from_env_flows_to_mcp_supervisor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_execute_agent_effect passes heartbeat_policy_from_env().interval to McpSupervisor."""
    from datetime import timedelta

    from ralph.process.mcp_supervisor import McpSupervisor

    config = _make_config_with_watchdog()
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    custom_interval = timedelta(milliseconds=750)
    captured_intervals: list[timedelta] = []

    original_init = McpSupervisor.__init__

    def patched_init(
        self: McpSupervisor,
        bridge: RestartAwareMcpBridge,
        *,
        check_interval: timedelta = timedelta(seconds=2),
        on_restart: Callable[[int], None] | None = None,
    ) -> None:
        captured_intervals.append(check_interval)
        original_init(self, bridge, check_interval=check_interval, on_restart=on_restart)

    monkeypatch.setattr(McpSupervisor, "__init__", patched_init)
    monkeypatch.setattr(
        runner_module,
        "heartbeat_policy_from_env",
        lambda: HeartbeatPolicy(interval=custom_interval),
    )

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

    monkeypatch.setattr(runner_module, "start_mcp_server", lambda *_a, **_kw: FakeBridge())
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _b: None)
    monkeypatch.setattr(
        runner_module,
        "materialize_system_prompt",
        lambda *, workspace_root, name: str(tmp_path / "SYSTEM_PROMPT.md"),
    )

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=lambda *_a, **_kw: [],
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )
    runner_module._execute_agent_effect(
        effect, config, deps, WorkspaceScope(tmp_path), display_context=make_display_context()
    )

    assert captured_intervals, "McpSupervisor was not constructed"
    assert captured_intervals[0] == custom_interval, (
        f"expected {custom_interval}, got {captured_intervals[0]}"
    )
