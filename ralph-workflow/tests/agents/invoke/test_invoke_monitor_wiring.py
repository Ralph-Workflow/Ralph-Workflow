from __future__ import annotations

import io
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog
from ralph.agents.invoke import invoke_agent
from ralph.agents.invoke._invoke_options import InvokeOptions
from ralph.agents.invoke._types import ResolvedInvocationRuntime
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.child_liveness import ChildLivenessSubagentPidSource
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.process.monitor import (
    DefaultProcessMonitor,
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
    ProcessMonitor,
    ProcessRole,
    SubagentOutputCapture,
    role_classifier_for_transport,
)
from ralph.testing.fake_process import (
    FakePopen,
    FakePsutil,
    ProcessState,
    ProcessStreams,
)

_FAKE_AGENT_PID = 10_000_000

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def _patch_resolve_invocation_runtime(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.agents.invoke.resolve_invocation_runtime",
        lambda *_args, **_kwargs: ResolvedInvocationRuntime(),
    )


def _capture_idle_watchdog_args(
    monkeypatch: MonkeyPatch,
    captured: dict[str, object],
) -> None:
    original_init = IdleWatchdog.__init__

    def _patched_init(self: IdleWatchdog, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(IdleWatchdog, "__init__", _patched_init)


@pytest.fixture(autouse=True)
def _fake_process_manager(monkeypatch: MonkeyPatch) -> None:

    def factory(_command: object, _options: object) -> FakePopen:
        return FakePopen(
            _FAKE_AGENT_PID,
            state=ProcessState(returncode=0),
            streams=ProcessStreams(stdout=io.StringIO("")),
        )

    manager = ProcessManager(
        policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False),
        sync_process_factory=factory,
        psutil=FakePsutil(),
    )
    monkeypatch.setattr("ralph.agents.invoke._process_reader.get_process_manager", lambda: manager)
    monkeypatch.setattr(
        "ralph.agents.invoke._run_shared_interactive_pty", _fake_shared_interactive_pty
    )


def _fake_shared_interactive_pty(*args: object, **kwargs: object) -> Iterator[str]:
    del kwargs
    ctx = args[1]
    config = ctx.config
    policy = ctx.policy
    monitor = DefaultProcessMonitor(
        1,
        role_classifier=role_classifier_for_transport(config.transport),
        discovery_strategy=NullDiscoveryStrategy(),
        poll_interval_seconds=policy.subagent_output_poll_interval_seconds,
    )
    IdleWatchdog(policy, FakeClock(), process_monitor=monitor)
    return iter(())


def _noop_command(
    _config: AgentConfig,
    _prompt_file: str,
    *,
    options: object,
) -> list[str]:
    return [
        "echo",
        "hello from agent",
    ]


@pytest.mark.timeout_seconds(1)
def test_monitor_wiring_fake_never_scans_the_machine_process_tree(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    _patch_resolve_invocation_runtime(monkeypatch)
    monkeypatch.setattr("ralph.agents.invoke._build_command", _noop_command)

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    list(invoke_agent(config, str(prompt_file)))

    monitor = captured.get("process_monitor")
    assert isinstance(monitor, DefaultProcessMonitor)
    assert monitor._host_pid == _FAKE_AGENT_PID


@pytest.mark.parametrize(
    "transport",
    [
        AgentTransport.CLAUDE,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.OPENCODE,
        AgentTransport.CODEX,
        AgentTransport.NANOCODER,
        AgentTransport.GENERIC,
        AgentTransport.AGY,
    ],
)
@pytest.mark.timeout_seconds(5)
def test_invoke_wires_process_monitor_for_transport(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    transport: AgentTransport,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    _patch_resolve_invocation_runtime(monkeypatch)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd=transport.value,
        transport=transport,
    )

    list(invoke_agent(config, str(prompt_file)))

    monitor = captured.get("process_monitor")
    assert monitor is not None
    assert isinstance(monitor, DefaultProcessMonitor)


@pytest.mark.parametrize(
    "transport",
    [
        AgentTransport.CLAUDE,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.CODEX,
        AgentTransport.NANOCODER,
        AgentTransport.GENERIC,
        AgentTransport.AGY,
    ],
)
@pytest.mark.timeout_seconds(5)
def test_invoke_wires_discovery_strategy_for_transport(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    transport: AgentTransport,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    _patch_resolve_invocation_runtime(monkeypatch)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd=transport.value,
        transport=transport,
    )

    list(invoke_agent(config, str(prompt_file)))

    monitor = captured.get("process_monitor")
    assert isinstance(monitor, DefaultProcessMonitor)
    assert isinstance(monitor._discovery_strategy, NullDiscoveryStrategy)


@pytest.mark.timeout_seconds(5)
def test_invoke_wires_opencode_registry_discovery_strategy(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    _patch_resolve_invocation_runtime(monkeypatch)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd=AgentTransport.OPENCODE.value,
        transport=AgentTransport.OPENCODE,
    )

    list(invoke_agent(config, str(prompt_file)))

    monitor = captured.get("process_monitor")
    assert isinstance(monitor, DefaultProcessMonitor)
    assert isinstance(monitor._discovery_strategy, OpenCodeRegistryDiscoveryStrategy)


@pytest.mark.parametrize(
    "transport",
    [
        AgentTransport.CLAUDE,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.OPENCODE,
        AgentTransport.CODEX,
        AgentTransport.NANOCODER,
        AgentTransport.GENERIC,
        AgentTransport.AGY,
    ],
)
@pytest.mark.timeout_seconds(5)
def test_invoke_role_classifier_is_conservative_for_transport(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    transport: AgentTransport,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    _patch_resolve_invocation_runtime(monkeypatch)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd=transport.value,
        transport=transport,
    )

    list(invoke_agent(config, str(prompt_file)))

    monitor = captured.get("process_monitor")
    assert isinstance(monitor, DefaultProcessMonitor)
    classifier = monitor._role_classifier
    assert classifier is role_classifier_for_transport(transport)
    assert classifier(123, ["worker", "--task", "agent"]) == ProcessRole.INCIDENTAL_HELPER
    assert classifier(123, [transport.value, "run", "hello"]) == ProcessRole.INCIDENTAL_HELPER


@pytest.mark.timeout_seconds(5)
def test_invoke_respects_disabled_monitor_flags(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
    )
    options = InvokeOptions(
        process_monitor_enabled=False,
        subagent_output_capture_enabled=False,
    )

    list(invoke_agent(config, str(prompt_file), options=options))

    assert captured.get("process_monitor") is None


@pytest.mark.timeout_seconds(5)
def test_invoke_disabling_only_process_monitor_keeps_discovery(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
    )
    options = InvokeOptions(process_monitor_enabled=False)

    list(invoke_agent(config, str(prompt_file), options=options))

    assert captured.get("process_monitor") is None


@pytest.mark.timeout_seconds(5)
def test_invoke_disabling_only_output_capture_keeps_process_monitor(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
    )
    options = InvokeOptions(subagent_output_capture_enabled=False)

    list(invoke_agent(config, str(prompt_file), options=options))

    monitor = captured.get("process_monitor")
    assert isinstance(monitor, DefaultProcessMonitor)
    assert monitor._discovery_strategy is None


@pytest.mark.timeout_seconds(5)
def test_invoke_wires_subagent_pid_source_for_opencode(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    _patch_resolve_invocation_runtime(monkeypatch)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
    )

    list(invoke_agent(config, str(prompt_file)))

    monitor = captured.get("process_monitor")
    assert isinstance(monitor, DefaultProcessMonitor)
    pid_source = monitor._subagent_pid_source
    assert isinstance(pid_source, ChildLivenessSubagentPidSource)


@pytest.mark.timeout_seconds(5)
def test_invoke_poll_interval_reaches_process_monitor(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    _capture_idle_watchdog_args(monkeypatch, captured)
    monkeypatch.setattr(
        "ralph.agents.invoke._build_command",
        _noop_command,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
    )
    options = InvokeOptions(subagent_output_poll_interval_seconds=2.5)

    list(invoke_agent(config, str(prompt_file), options=options))

    monitor = captured.get("process_monitor")
    assert isinstance(monitor, DefaultProcessMonitor)
    assert monitor._poll_interval_seconds == 2.5


class _FreshOutputCapture(SubagentOutputCapture):
    def read_lines(self, worker_id: str) -> list[str]:
        return ["fresh subagent output"]


class _FreshProcessMonitor(ProcessMonitor):
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {"worker-1": _FreshOutputCapture()}


@pytest.mark.timeout_seconds(5)
def test_invoke_fresh_subagent_output_defers_no_output_deadline(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.invoke._command_builders.OpencodeCommandBuilder.build",
        lambda self, _config, _prompt_file, *, options: [
            "sleep",
            "0.15",
        ],
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._process_reader._make_process_monitor",
        lambda _handle, _config, _policy, _pid_source=None, **_kwargs: _FreshProcessMonitor(),
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    config = AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
    )
    options = InvokeOptions(
        idle_timeout_seconds=0.05,
        drain_window_seconds=0.1,
        activity_evidence_ttl_seconds=1.0,
        subagent_output_poll_interval_seconds=0.02,
        child_exit_reconcile_seconds=0.05,
        process_exit_wait_seconds=2.0,
    )

    lines = list(invoke_agent(config, str(prompt_file), options=options, _clock=FakeClock()))
    assert lines == []
