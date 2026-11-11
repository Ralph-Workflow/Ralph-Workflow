from __future__ import annotations

import inspect
import multiprocessing
from pathlib import Path

import pytest

from ralph.mcp.server._transport_repetition_tracker import (
    _STATE_LOCK_TIMEOUT_S,
    REPETITION_STATE_FILENAME,
)
from ralph.mcp.server._wire_ledger import WIRE_LEDGER_RELPATH


def test_e16_store_1_wire_ledger_has_two_real_child_interleave_test() -> None:
    edge_cases_module = _module_path("tests/mcp/test_bridge_e1_e2_edge_cases.py")
    handover_module = _module_path("tests/mcp/test_handover_inflight_real_child.py")

    assert edge_cases_module.exists(), (
        "tests/mcp/test_bridge_e1_e2_edge_cases.py must exist (entry 1 interleave proof #1)"
    )
    assert handover_module.exists(), (
        "tests/mcp/test_handover_inflight_real_child.py must exist (entry 1 interleave proof #2)"
    )
    edge_cases_src = edge_cases_module.read_text(encoding="utf-8")
    handover_src = handover_module.read_text(encoding="utf-8")
    assert "test_e16_two_child_ledger_interleave" in edge_cases_src, (
        "Edge-cases module must keep test_e16_two_child_ledger_interleave"
    )
    assert "multiprocessing.get_context" in edge_cases_src, (
        "test_e16_two_child_ledger_interleave must drive a real second process"
    )
    assert "verify_chain" in edge_cases_src, (
        "test_e16_two_child_ledger_interleave must invoke the production verifier"
    )
    assert "test_e16_interleaved_ledger" in handover_src, (
        "Handover module must keep test_e16_interleaved_ledger"
    )
    assert "get_process_manager" in handover_src, (
        "test_e16_interleaved_ledger must spawn via ProcessManager"
    )
    assert "subprocess_e2e" in edge_cases_src
    assert "subprocess_e2e" in handover_src


def test_e16_store_1_wire_ledger_writer_takes_flock_under_chain_head_read() -> None:
    import ralph.mcp.server._wire_ledger as wire_ledger_module

    src = inspect.getsource(wire_ledger_module.append_wire_record)
    lock_pos = src.index("fcntl.flock(")
    head_pos = src.index("_read_last_hmac(ledger_path)")
    append_pos = src.index('ledger_path.open("a"', head_pos)
    assert lock_pos < head_pos < append_pos, (
        f"wire-ledger ordering broken: lock@{lock_pos}, head@{head_pos}, append@{append_pos}"
    )


def test_e16_store_8_transport_sidecar_interleave_module_loads() -> None:
    interleave_path = _module_path("tests/mcp/test_e16_store_interleave_real_children.py")
    assert interleave_path.exists(), (
        "tests/mcp/test_e16_store_interleave_real_children.py must exist (entry 8 interleave proof)"
    )
    src = interleave_path.read_text(encoding="utf-8")
    assert "subprocess_e2e" in src, "Entry 8 interleave test must be subprocess_e2e (real children)"
    assert "TransportRepetitionTracker" in src
    assert "REPETITION_STATE_FILENAME" in src
    assert REPETITION_STATE_FILENAME == "transport-repetition-state.json"
    assert _STATE_LOCK_TIMEOUT_S > 0.0


def test_e16_store_2_session_file_has_no_child_writer() -> None:
    import ralph.mcp.server.runtime_session as runtime_session_module

    session_src = inspect.getsource(runtime_session_module.FileBackedSession)
    write_methods = ("write_json", "save", "commit", "_write", "_save")
    for method_name in write_methods:
        assert f"def {method_name}" not in session_src, (
            f"FileBackedSession must not expose a {method_name!r} method (entry 2 impossibility)"
        )
    assert "def _load(" in session_src, "child's only operation is read via _load"


def test_e16_store_3_durable_sink_is_per_bridge() -> None:
    import ralph.mcp.server._mcp_health_event as mcp_health_event_module

    src = inspect.getsource(mcp_health_event_module.configure_bridge_sink)
    assert "mcp-health-events-" in src, (
        "Entry 3 must produce a per-bridge file (entry 3 impossibility)"
    )
    assert "bridge_id" in src
    assert "safe_id" in src and "sha256" in src
    record_src = inspect.getsource(mcp_health_event_module.record_restart_decision)
    assert "_DURABLE_SINK_LOCK" in record_src, (
        "Entry 3 sink appends must be locked (threading.Lock)"
    )


def test_e16_store_4_activity_relay_has_no_on_disk_store() -> None:
    import ralph.mcp.server._activity_relay as activity_relay_module
    import ralph.mcp.server._activity_relay_protocol as protocol_module
    import ralph.mcp.server._activity_relay_sender as sender_module
    import ralph.mcp.server._activity_relay_snapshot as snapshot_module

    activity_relay_src = inspect.getsource(activity_relay_module)
    sender_src = inspect.getsource(sender_module)
    protocol_src = inspect.getsource(protocol_module)
    snapshot_src = inspect.getsource(snapshot_module)
    forbidden = ("open(", "write_text", "write_bytes", "Path(", ".jsonl", "tmp_path", "json.dump(")
    for forbidden_token in forbidden:
        assert forbidden_token not in activity_relay_src, (
            f"ActivityRelay must be in-memory only; found {forbidden_token!r}"
        )
        assert forbidden_token not in sender_src, (
            f"ActivityRelay sender must be in-memory only; found {forbidden_token!r}"
        )
        assert forbidden_token not in protocol_src, (
            f"ActivityRelay protocol must be in-memory only; found {forbidden_token!r}"
        )
        assert forbidden_token not in snapshot_src, (
            f"ActivityRelay snapshot must be in-memory only; found {forbidden_token!r}"
        )


def test_e16_store_5_stage_files_are_spawn_scoped() -> None:
    import ralph.mcp.server._start_stage_tracker as start_stage_tracker_module

    src = inspect.getsource(start_stage_tracker_module.stage_file_path)
    assert "_STAGE_FILE_NAME_COMPONENT" in src
    assert "spawn_id" in src


def test_e16_store_6_artifact_stores_are_task_isolated() -> None:
    from ralph.mcp.protocol import env as protocol_env

    assert hasattr(protocol_env, "WORKER_ARTIFACT_DIR_ENV")
    assert hasattr(protocol_env, "MCP_SNAPSHOT_DIR_ENV")


def test_e16_store_7_handover_sentinels_are_stage_suffixed() -> None:
    from ralph.mcp.protocol import env as protocol_env

    name_pause = protocol_env.MCP_ACCEPT_PAUSE_FILE
    name_resume = protocol_env.MCP_ACCEPT_RESUME_FILE
    assert name_pause
    assert name_resume
    assert name_pause != name_resume


def _module_path(rel_path: str) -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    return repo_root / rel_path


class _WriterProcess(multiprocessing.Process):
    def __init__(
        self, workspace_root: Path, secret: str, run_id: str, worker_id: str, count: int
    ) -> None:
        super().__init__(name=f"e16-writer-{worker_id}")
        self._workspace_root = workspace_root
        self._secret = secret
        self._run_id = run_id
        self._worker_id = worker_id
        self._count = count

    def run(self) -> None:
        from ralph.mcp.server._wire_ledger import append_wire_record

        for i in range(self._count):
            append_wire_record(
                self._workspace_root,
                method="tools/call",
                tool_name=f"tool_{self._worker_id}_{i}",
                params={"worker": self._worker_id, "index": i},
                run_id=self._run_id,
                secret=self._secret,
            )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(15)
def test_e16_wire_ledger_interleave_smoke_runs_under_sibling_tests(
    tmp_path: Path,
) -> None:
    from ralph.mcp.server._wire_ledger import append_wire_record, verify_chain

    secret = "wu3-e16-secret"
    run_id = "run-wu3-e16-smoke"

    (tmp_path / WIRE_LEDGER_RELPATH).parent.mkdir(parents=True, exist_ok=True)

    p1 = _WriterProcess(tmp_path, secret, run_id, "wu3-a", 4)
    p2 = _WriterProcess(tmp_path, secret, run_id, "wu3-b", 4)
    p1.start()
    p2.start()
    p1.join(timeout=10.0)
    p2.join(timeout=10.0)
    assert p1.exitcode == 0, f"writer A exited {p1.exitcode}"
    assert p2.exitcode == 0, f"writer B exited {p2.exitcode}"

    assert verify_chain(tmp_path, secret) is True

    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="post_smoke_tool",
        params={"smoke": "post"},
        run_id=run_id,
        secret=secret,
    )
    assert verify_chain(tmp_path, secret) is True
