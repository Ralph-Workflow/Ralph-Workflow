"""Integration tests for process-monitor/discovery wiring in the invoke path."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog
from ralph.agents.invoke import invoke_agent
from ralph.agents.invoke._invoke_options import InvokeOptions
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.monitor import (
    DefaultProcessMonitor,
    DiscoveryStrategy,
    OpencodeSubagentOutputDiscovery,
    SubagentOutputCapture,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


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


def test_invoke_wires_process_monitor_and_discovery(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-06/AC-07/AC-09/AC-10: real invoke path injects monitor and discovery."""
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

    list(invoke_agent(config, str(prompt_file)))

    assert captured.get("process_monitor") is not None
    assert isinstance(captured["process_monitor"], DefaultProcessMonitor)
    assert captured.get("discovery_strategy") is not None
    assert isinstance(captured["discovery_strategy"], OpencodeSubagentOutputDiscovery)


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
    assert captured.get("discovery_strategy") is None


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
    assert isinstance(captured.get("discovery_strategy"), OpencodeSubagentOutputDiscovery)


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

    assert isinstance(captured.get("process_monitor"), DefaultProcessMonitor)
    assert captured.get("discovery_strategy") is None


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


class _FreshDiscovery(DiscoveryStrategy):
    """Discovery that always reports one fresh worker output stream."""

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
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
        "ralph.agents.invoke._commands._build_opencode_command",
        lambda _config, _prompt_file, *, options: [
            "python",
            "-c",
            "import time; time.sleep(0.15)",
        ],
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._process_reader._make_discovery_strategy",
        lambda _config, _policy: _FreshDiscovery(),
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._process_reader._make_process_monitor",
        lambda _handle, _policy: None,
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
