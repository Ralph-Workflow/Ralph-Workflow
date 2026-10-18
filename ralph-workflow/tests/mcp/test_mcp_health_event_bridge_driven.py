"""Bridge-driven health event tests verifying per-bridge durable sink records.

Forces both restart decisions through the real bridge path:
1. process_exited: when inner process exits, bridge records reason='process_exited',
   exit_status, bounded log_tail, and stage='process_exited' to configured per-bridge sink.
2. probe_failed: when probe fails repeatedly under normal conditions (no contention),
   bridge records reason='probe_failed', probe_duration_s, bounded log_tail, and
   stage='probe_failed' to configured per-bridge sink.
"""

from __future__ import annotations

import pathlib
import tempfile
from datetime import timedelta

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_health_event import (
    configure_bridge_sink,
    read_durable_sink,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._server_log import mcp_server_log_path


class _FakeProcess:
    def __init__(self, poll_result: int | None = None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None


def _make_inner(poll_result: int | None = None, pid: int = 9999) -> lifecycle.StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9300/mcp",
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


def test_bridge_driven_process_exited_records_to_per_bridge_sink(tmp_path: pathlib.Path) -> None:
    bridge_id = "test-bridge-exited-123"
    sink_path = configure_bridge_sink(tmp_path, bridge_id)
    assert sink_path.name == f"mcp-health-events-{bridge_id}.jsonl"

    log_file = mcp_server_log_path(tmp_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_lines = [f"crash line {i}" for i in range(1, 16)]
    log_file.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    inner = _make_inner(poll_result=137)
    restarted_inner = _make_inner(poll_result=None)

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: restarted_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-process-exited",
        workspace_root=tmp_path,
    )
    bridge._durable_sink_path = sink_path

    # Force decision through the real bridge health-check path
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1

    records = read_durable_sink(path=sink_path)
    assert len(records) == 1
    record = records[0]

    assert record["reason"] == "process_exited"
    assert record["exit_status"] == 137
    assert record["probe_duration_s"] is None
    assert record["stage"] == "process_exited"

    # Log tail must be bounded to at most 10 lines
    assert record["log_tail"] is not None
    assert isinstance(record["log_tail"], list)
    assert len(record["log_tail"]) == 10
    assert record["log_tail"] == [f"crash line {i}" for i in range(6, 16)]


def test_bridge_driven_probe_failed_records_to_per_bridge_sink(tmp_path: pathlib.Path) -> None:
    bridge_id = "test-bridge-probe-456"
    sink_path = configure_bridge_sink(tmp_path, bridge_id)
    assert sink_path.name == f"mcp-health-events-{bridge_id}.jsonl"

    log_file = mcp_server_log_path(tmp_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_lines = [f"stalled line {i}" for i in range(1, 16)]
    log_file.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    inner = _make_inner(poll_result=None)
    restarted_inner = _make_inner(poll_result=None)

    def failing_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe timed out / stalled")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: restarted_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-probe-failed",
        probe_fn=failing_probe,
        workspace_root=tmp_path,
        contention_signal_producer=lambda: False,
    )
    bridge._durable_sink_path = sink_path

    # First 2 misses: no restart yet (3-miss policy)
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert len(read_durable_sink(path=sink_path)) == 0

    # 3rd miss: triggers restart on probe_failed
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1

    records = read_durable_sink(path=sink_path)
    assert len(records) == 1
    record = records[0]

    assert record["reason"] == "probe_failed"
    assert record["exit_status"] is None
    assert isinstance(record["probe_duration_s"], (float, int))
    assert record["probe_duration_s"] >= 0.0
    assert record["stage"] == "probe_failed"

    # Log tail must be bounded to at most 10 lines
    assert record["log_tail"] is not None
    assert isinstance(record["log_tail"], list)
    assert len(record["log_tail"]) == 10
    assert record["log_tail"] == [f"stalled line {i}" for i in range(6, 16)]


def test_bridge_driven_both_decisions_append_to_same_sink(tmp_path: pathlib.Path) -> None:
    bridge_id = "test-bridge-both-789"
    sink_path = configure_bridge_sink(tmp_path, bridge_id)

    log_file = mcp_server_log_path(tmp_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("initial log\n", encoding="utf-8")

    inner = _make_inner(poll_result=None)
    restarted_1 = _make_inner(poll_result=None)
    restarted_2 = _make_inner(poll_result=None)
    restarts = [restarted_1, restarted_2]

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        return restarts.pop(0)

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-both",
        probe_fn=lambda ep, to: None,
        workspace_root=tmp_path,
    )
    bridge._durable_sink_path = sink_path

    # 1. Force process_exited decision
    inner.process.terminate()
    assert bridge.check_health_and_restart_if_needed() is True
    assert bridge.restart_count == 1

    # 2. Force probe_failed decision on the newly restarted inner
    def failing_probe(ep: str, to: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge._probe_fn = failing_probe
    bridge._contention_signal_producer = lambda: False
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 2

    records = read_durable_sink(path=sink_path)
    assert len(records) == 2
    assert records[0]["reason"] == "process_exited"
    assert records[0]["exit_status"] == 0
    assert records[0]["stage"] == "process_exited"

    assert records[1]["reason"] == "probe_failed"
    assert records[1]["exit_status"] is None
    assert isinstance(records[1]["probe_duration_s"], (float, int))
    assert records[1]["stage"] == "probe_failed"
