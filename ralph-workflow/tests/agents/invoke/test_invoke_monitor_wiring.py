"""Integration tests for process-monitor/discovery wiring in the invoke path."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog
from ralph.agents.invoke import invoke_agent
from ralph.agents.invoke._invoke_options import InvokeOptions
from ralph.agents.invoke._types import ResolvedInvocationRuntime
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.child_liveness import ChildLivenessSubagentPidSource
from ralph.process.monitor import (
    ClaudeCodeSubagentOutputDiscovery,
    DefaultProcessMonitor,
    OpencodeSubagentOutputDiscovery,
    ProcessMonitor,
    ProcessRole,
    SubagentOutputCapture,
    role_classifier_for_transport,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def _patch_resolve_invocation_runtime(monkeypatch: MonkeyPatch) -> None:
    """Return a minimal runtime so transport-specific MCP setup is not exercised.

    These tests verify process-monitor wiring, not runtime resolution. A minimal
    runtime keeps the tests focused and avoids network/file setup that some
    transports require when a real MCP endpoint is present.
    """
    monkeypatch.setattr(
        "ralph.agents.invoke.resolve_invocation_runtime",
        lambda *_args, **_kwargs: ResolvedInvocationRuntime(),
    )


def _capture_idle_watchdog_args(
    monkeypatch: MonkeyPatch,
    captured: dict[str, object],
) -> None:
    """Patch IdleWatchdog.__init__ to record its keyword arguments."""
    original_init = IdleWatchdog.__init__

    def _patched_init(self: IdleWatchdog, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(IdleWatchdog, "__init__", _patched_init)


def _noop_command(
    _config: AgentConfig,
    _prompt_file: str,
    *,
    options: object,
) -> list[str]:
    """Return a fast Python command so the invocation completes quickly."""
    return [
        "python",
        "-c",
        "print('hello from agent')",
    ]


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
def test_invoke_wires_process_monitor_for_transport(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    transport: AgentTransport,
) -> None:
    """AC-06/AC-09/AC-10: real invoke path injects a DefaultProcessMonitor for every transport."""
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
    ("transport", "expected_discovery"),
    [
        (AgentTransport.CLAUDE, ClaudeCodeSubagentOutputDiscovery),
        (AgentTransport.CLAUDE_INTERACTIVE, ClaudeCodeSubagentOutputDiscovery),
        (AgentTransport.OPENCODE, OpencodeSubagentOutputDiscovery),
        (AgentTransport.CODEX, type(None)),
        (AgentTransport.NANOCODER, type(None)),
        (AgentTransport.GENERIC, type(None)),
        (AgentTransport.AGY, type(None)),
    ],
)
def test_invoke_wires_discovery_strategy_for_transport(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    transport: AgentTransport,
    expected_discovery: type,
) -> None:
    """AC-10/AC-11: only documented transports get a discovery strategy."""
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
    if expected_discovery is type(None):
        assert monitor._discovery_strategy is None
    else:
        assert isinstance(monitor._discovery_strategy, expected_discovery)


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
def test_invoke_role_classifier_is_conservative_for_transport(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    transport: AgentTransport,
) -> None:
    """AC-06/AC-10/AC-11: command-line classifier is documentation-grounded and conservative.

    No supported transport documents a stable external subagent-identification
    command-line signal. The command-line classifier must therefore not promote
    descendants to SPAWNED_SUBAGENT based on broad tokens. OpenCode subagents
    are identified separately via the injected SubagentPidSource.
    """
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


def test_invoke_respects_disabled_monitor_flags(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-10: disabling monitor/output capture yields None dependencies."""
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


def test_invoke_disabling_only_process_monitor_keeps_discovery(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-10: process_monitor_enabled=false does not disable output capture."""
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


def test_invoke_disabling_only_output_capture_keeps_process_monitor(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-10: subagent_output_capture_enabled=false does not disable the monitor."""
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


def test_invoke_wires_subagent_pid_source_for_opencode(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-06/AC-09/AC-10/AC-11: OpenCode invocation wires a SubagentPidSource.

    The source is backed by the ChildLivenessRegistry so real OpenCode child
    lifecycle events (which carry PIDs) can identify spawned subagents.
    """
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


def test_invoke_poll_interval_reaches_process_monitor(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-10: subagent_output_poll_interval_seconds is passed to DefaultProcessMonitor."""
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
    """Test capture that returns a fresh line on every poll."""

    def read_lines(self, worker_id: str) -> list[str]:
        return ["fresh subagent output"]


class _FreshProcessMonitor(ProcessMonitor):
    """Process monitor that always reports one fresh worker output stream."""

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
    """AC-07: real invoke path defers NO_OUTPUT_DEADLINE via discovered subagent output.

    The agent command produces no stdout and sleeps longer than the idle timeout.
    Without fresh subagent output the watchdog would fire; with the injected
    discovery strategy reporting fresh output each poll, the run completes.
    """
    monkeypatch.setattr(
        "ralph.agents.invoke._command_builders.OpencodeCommandBuilder.build",
        lambda self, _config, _prompt_file, *, options: [
            "python",
            "-c",
            "import time; time.sleep(0.15)",
        ],
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._process_reader._make_process_monitor",
        lambda _handle, _config, _policy, _pid_source=None: _FreshProcessMonitor(),
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

    lines = list(invoke_agent(config, str(prompt_file), options=options))
    assert lines == []
