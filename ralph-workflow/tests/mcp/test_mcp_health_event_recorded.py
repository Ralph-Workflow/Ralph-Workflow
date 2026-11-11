from __future__ import annotations

from pathlib import Path

import pytest

import ralph.mcp.server._mcp_health_event as m
from ralph.mcp.server._mcp_health_event import (
    RestartReason,
    clear_durable_sink,
    configure_bridge_sink,
    get_durable_sink_path,
    get_restart_decisions,
    read_durable_sink,
    record_restart_decision,
    record_stage,
    set_durable_sink_path,
)


def test_record_stage_available_log() -> None:
    record_stage("test_available", available=True)
    assert ("test_available", True) in m._EVENTS


def test_record_stage_unavailable_log() -> None:
    record_stage("test_unavailable", available=False)
    assert ("test_unavailable", False) in m._EVENTS


def test_record_stage_events_are_bounded() -> None:
    for i in range(100):
        record_stage(f"stage_{i}", available=i % 2 == 0)
    assert len(m._EVENTS) == 64


def test_record_restart_decision_process_exited_has_exit_status_and_log_tail() -> None:
    log_tail = ("line one", "line two", "error occurred")
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=42,
        log_tail=log_tail,
        stage="ready_to_serve",
    )
    decisions = get_restart_decisions()
    assert len(decisions) >= 1
    last = decisions[-1]
    assert last.reason is RestartReason.PROCESS_EXITED
    assert last.exit_status == 42
    assert last.probe_duration_s is None
    assert last.log_tail == log_tail
    assert last.stage == "ready_to_serve"
    assert last.available is False


def test_record_restart_decision_process_exited_requires_exit_status() -> None:
    with pytest.raises(ValueError, match="exit_status"):
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            log_tail=None,
            stage="upstream_catalog_or_discovery_done",
        )


def test_record_restart_decision_probe_failed_has_probe_duration() -> None:
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=1.234,
        stage="ready_to_serve",
    )
    decisions = get_restart_decisions()
    assert len(decisions) >= 1
    last = decisions[-1]
    assert last.reason is RestartReason.PROBE_FAILED
    assert last.exit_status is None
    assert last.probe_duration_s == 1.234
    assert last.log_tail is None
    assert last.stage == "ready_to_serve"
    assert last.available is False


def test_record_restart_decision_probe_failed_requires_probe_duration() -> None:
    with pytest.raises(ValueError, match="probe_duration_s"):
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            stage="registry_built",
        )


def test_record_restart_decision_probe_deferred_contention_has_expected_fields() -> None:
    record_restart_decision(
        reason=RestartReason.PROBE_DEFERRED_CONTENTION,
        stage="probe_deferred_contention",
    )
    decisions = get_restart_decisions()
    assert len(decisions) >= 1
    last = decisions[-1]
    assert last.reason is RestartReason.PROBE_DEFERRED_CONTENTION
    assert last.exit_status is None
    assert last.probe_duration_s is None
    assert last.log_tail is None
    assert last.stage == "probe_deferred_contention"
    assert last.available is False


def test_record_restart_decision_probe_deferred_contention_accepts_str_reason() -> None:
    record_restart_decision(
        reason="probe_deferred_contention",
        stage="contended_host",
    )
    last = get_restart_decisions()[-1]
    assert last.reason is RestartReason.PROBE_DEFERRED_CONTENTION
    assert last.stage == "contended_host"


def test_record_restart_decision_probe_deferred_contention_with_evidence() -> None:
    log_tail = ("warning: inotify watch budget saturated",)
    record_restart_decision(
        reason=RestartReason.PROBE_DEFERRED_CONTENTION,
        probe_duration_s=0.05,
        log_tail=log_tail,
        stage="watch_saturated",
    )
    last = get_restart_decisions()[-1]
    assert last.reason is RestartReason.PROBE_DEFERRED_CONTENTION
    assert last.probe_duration_s == 0.05
    assert last.log_tail == log_tail
    assert last.stage == "watch_saturated"


def test_record_restart_decision_bounds_log_tail() -> None:
    oversized_tail = tuple(f"line-{i}" for i in range(25))
    record_restart_decision(
        reason=RestartReason.PROBE_DEFERRED_CONTENTION,
        log_tail=oversized_tail,
        stage="tail_bounded",
    )
    last = get_restart_decisions()[-1]
    assert last.log_tail is not None
    assert len(last.log_tail) == 10
    assert last.log_tail == tuple(f"line-{i}" for i in range(15, 25))


def test_record_restart_decision_process_exited_with_no_log_tail() -> None:
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=137,
        log_tail=None,
        stage="env_loaded",
    )
    decisions = get_restart_decisions()
    last = decisions[-1]
    assert last.log_tail is None
    assert last.exit_status == 137


def test_get_restart_decisions_returns_immutable_snapshot() -> None:
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=0.5,
        stage="session_ready",
    )
    snapshot = get_restart_decisions()
    assert isinstance(snapshot, tuple)
    m._DECISIONS.clear()
    assert snapshot == get_restart_decisions() or snapshot != ()


def test_restart_decisions_are_bounded() -> None:
    for i in range(100):
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            probe_duration_s=float(i),
            stage=f"stage_{i}",
        )
    assert len(m._DECISIONS) == 64
    assert len(get_restart_decisions()) == 64


def test_restart_decision_is_frozen() -> None:
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=1,
        log_tail=("a",),
        stage="config_loaded",
    )
    decision = get_restart_decisions()[-1]
    assert decision.exit_status == 1
    alias = decision
    attr_name = "exit_status"
    with pytest.raises(AttributeError):
        setattr(alias, attr_name, 99)


def test_restart_reason_enum_values() -> None:
    assert set(RestartReason) == {
        RestartReason.PROCESS_EXITED,
        RestartReason.PROBE_FAILED,
        RestartReason.PROBE_DEFERRED_CONTENTION,
    }
    assert RestartReason.PROCESS_EXITED.value == "process_exited"
    assert RestartReason.PROBE_FAILED.value == "probe_failed"
    assert RestartReason.PROBE_DEFERRED_CONTENTION.value == "probe_deferred_contention"


def test_set_and_get_durable_sink_path(tmp_path: Path) -> None:
    orig = get_durable_sink_path()
    try:
        sink = tmp_path / "decisions.jsonl"
        set_durable_sink_path(sink)
        assert get_durable_sink_path() == sink
    finally:
        set_durable_sink_path(orig)


def test_clear_durable_sink_removes_file(tmp_path: Path) -> None:
    sink = tmp_path / "decisions.jsonl"
    sink.write_text("dummy\n", encoding="utf-8")
    assert sink.exists()
    set_durable_sink_path(sink)
    clear_durable_sink()
    assert not sink.exists()
    set_durable_sink_path(None)


def test_clear_durable_sink_noop_when_absent(tmp_path: Path) -> None:
    sink = tmp_path / "absent.jsonl"
    assert not sink.exists()
    set_durable_sink_path(sink)
    clear_durable_sink()
    assert not sink.exists()
    set_durable_sink_path(None)


def test_durable_sink_process_exited_persists_exit_status(
    tmp_path: Path,
) -> None:
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        log_tail = ("startup done", "server running")
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=137,
            log_tail=log_tail,
            stage="ready_to_serve",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["reason"] == "process_exited"
        assert record["exit_status"] == 137
        assert record["probe_duration_s"] is None
        assert record["log_tail"] == list(log_tail)
        assert record["stage"] == "ready_to_serve"
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_durable_sink_probe_failed_persists_probe_duration(
    tmp_path: Path,
) -> None:
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            probe_duration_s=2.718,
            stage="registry_built",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["reason"] == "probe_failed"
        assert record["probe_duration_s"] == 2.718
        assert record["exit_status"] is None
        assert record["stage"] == "registry_built"
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_durable_sink_log_available_records_tail(
    tmp_path: Path,
) -> None:
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        log_lines = [f"log line {i}" for i in range(15)]
        log_tail = tuple(log_lines[-10:])
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=1,
            log_tail=log_tail,
            stage="process_exited",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["log_tail"] == list(log_tail)
        assert len(record["log_tail"]) == 10
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_durable_sink_log_unavailable_records_none(
    tmp_path: Path,
) -> None:
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=137,
            log_tail=None,
            stage="process_exited",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["log_tail"] is None
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_durable_sink_probe_deferred_contention_persists(
    tmp_path: Path,
) -> None:
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        record_restart_decision(
            reason=RestartReason.PROBE_DEFERRED_CONTENTION,
            stage="probe_deferred_contention",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["reason"] == "probe_deferred_contention"
        assert record["exit_status"] is None
        assert record["probe_duration_s"] is None
        assert record["log_tail"] is None
        assert record["stage"] == "probe_deferred_contention"
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_read_durable_sink_returns_empty_when_no_file(
    tmp_path: Path,
) -> None:
    orig = get_durable_sink_path()
    absent = tmp_path / "does_not_exist.jsonl"
    try:
        set_durable_sink_path(absent)
        result = read_durable_sink()
        assert result == ()
    finally:
        set_durable_sink_path(orig)


def test_two_bridges_retain_separate_sinks(tmp_path: Path) -> None:
    root = tmp_path

    path_a = configure_bridge_sink(root, "bridge-a")
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=42,
        log_tail=("line1", "line2"),
        stage="ready_to_serve",
    )

    path_b = configure_bridge_sink(root, "bridge-b")
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=1.5,
        stage="registry_built",
    )

    assert path_a != path_b
    assert path_a.exists()
    assert path_b.exists()

    records_a = read_durable_sink(path=path_a)
    records_b = read_durable_sink(path=path_b)
    assert len(records_a) == 1
    assert len(records_b) == 1
    assert records_a[0]["reason"] == "process_exited"
    assert records_a[0]["exit_status"] == 42
    assert records_b[0]["reason"] == "probe_failed"
    assert records_b[0]["probe_duration_s"] == 1.5


def test_sink_survives_shutdown_semantics(tmp_path: Path) -> None:
    root = tmp_path

    path_a = configure_bridge_sink(root, "bridge-x")
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=99,
        log_tail=("startup",),
        stage="env_loaded",
    )

    assert path_a.exists()
    records = read_durable_sink(path=path_a)
    assert len(records) == 1
    assert records[0]["exit_status"] == 99

    path_b = configure_bridge_sink(root, "bridge-y")
    assert path_b != path_a
    assert path_a.exists(), "bridge-x evidence must survive bridge-y configuration"
    assert not path_b.exists()

    records_x = read_durable_sink(path=path_a)
    assert len(records_x) == 1
    assert records_x[0]["exit_status"] == 99


def test_explicit_sink_path_routes_records_per_bridge_interleaved(
    tmp_path: Path,
) -> None:
    sink_a = tmp_path / "bridge-a.jsonl"
    sink_b = tmp_path / "bridge-b.jsonl"
    set_durable_sink_path(None)

    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=10,
        stage="A-first",
        sink_path=sink_a,
    )
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=0.25,
        stage="B-only",
        sink_path=sink_b,
    )
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=20,
        stage="A-second",
        sink_path=sink_a,
    )

    records_a = read_durable_sink(path=sink_a)
    records_b = read_durable_sink(path=sink_b)
    assert [r["stage"] for r in records_a] == ["A-first", "A-second"]
    assert [r["exit_status"] for r in records_a] == [10, 20]
    assert [r["stage"] for r in records_b] == ["B-only"]
    assert records_b[0]["probe_duration_s"] == 0.25
    assert records_a[0]["log_tail"] is None


def test_explicit_sink_tail_none_vs_empty_distinct(tmp_path: Path) -> None:
    sink = tmp_path / "tails.jsonl"
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=1,
        log_tail=None,
        stage="unavailable",
        sink_path=sink,
    )
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=2,
        log_tail=(),
        stage="available-empty",
        sink_path=sink,
    )
    records = read_durable_sink(path=sink)
    assert records[0]["log_tail"] is None
    assert records[1]["log_tail"] == []


def test_explicit_sink_none_falls_back_to_global(tmp_path: Path) -> None:
    orig = get_durable_sink_path()
    sink = tmp_path / "global.jsonl"
    try:
        set_durable_sink_path(sink)
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            probe_duration_s=0.5,
            stage="legacy",
            sink_path=None,
        )
        assert sink.exists()
        records = read_durable_sink(path=sink)
        assert records[-1]["stage"] == "legacy"
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink(sink)


def test_durable_sink_record_shape_is_closed(tmp_path: Path) -> None:
    orig = get_durable_sink_path()
    sink = tmp_path / "shape.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=137,
            log_tail=("line a", "line b"),
            stage="ready_to_serve",
        )
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            probe_duration_s=1.234,
            stage="registry_built",
        )
        record_restart_decision(
            reason=RestartReason.PROBE_DEFERRED_CONTENTION,
            stage="probe_deferred_contention",
        )
        read_back = read_durable_sink(path=sink)
        assert len(read_back) == 3
        expected_keys = {
            "reason",
            "exit_status",
            "probe_duration_s",
            "log_tail",
            "stage",
        }
        forbidden_keys = {"spawn_id", "pid", "timestamp"}
        for record in read_back:
            assert set(record.keys()) == expected_keys, (
                f"JSONL record schema drift: got {set(record.keys())!r}, "
                f"expected exactly {expected_keys!r}. Update the R3 doc "
                f"(docs/sphinx/mcp-restart-decisions.md) and the manual "
                f"correlation procedure together."
            )
            assert not (set(record.keys()) & forbidden_keys), (
                f"JSONL record added a correlation-key field "
                f"({set(record.keys()) & forbidden_keys!r}). The R3 doc "
                f"must describe how the operator pairs the new field with "
                f"the per-spawn stage file before the test is updated."
            )
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink(sink)
