"""Native OpenCode subagent evidence read from OpenCode's session store.

OpenCode 1.18.x emits ``step_start`` when the parent dispatches a native
``task`` and then nothing until the child finishes, so the stream carries no
first-party evidence while a subagent works. The probe under test reads the
child sessions OpenCode persists as it works and forwards each updated part
as demonstrated child work. These tests drive the probe through an in-memory
part source and a fake clock; the SQLite reader is exercised against a
throwaway store under ``tmp_path`` built with OpenCode's real column names.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ralph.agents.invoke.opencode_subagent_sessions import (
    OpenCodeChildPart,
    OpenCodeSubagentSessionProbe,
    SqliteOpenCodeChildPartSource,
    default_opencode_db_path,
    part_kind_from_data,
    summarize_child_part,
)


class _FakeSource:
    def __init__(self, parts: list[OpenCodeChildPart]) -> None:
        self.parts = parts
        self.calls: list[tuple[str, int]] = []
        self.closed = False

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        self.calls.append((parent_session_id, since_ms))
        return [part for part in self.parts if part.time_updated_ms >= since_ms]

    def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now


def _part(part_id: str, updated_ms: int, *, kind: str = "tool:ralph_read_file") -> OpenCodeChildPart:
    return OpenCodeChildPart(
        child_session_id="ses_child",
        agent="Sisyphus-Junior",
        title="Implement S7 history feed",
        part_id=part_id,
        kind=kind,
        time_updated_ms=updated_ms,
    )


def _probe(
    source: _FakeSource,
    clock: _Clock,
    *,
    parent: str | None = "ses_parent",
    poll_interval: float = 2.0,
) -> tuple[OpenCodeSubagentSessionProbe, list[str], list[str]]:
    summaries: list[str] = []
    children: list[str] = []
    probe = OpenCodeSubagentSessionProbe(
        source=source,
        parent_session_id=lambda: parent,
        subagent_sink=summaries.append,
        child_progress_sink=children.append,
        monotonic=clock.monotonic,
        wall_clock_ms=lambda: 5_000,
        poll_interval_seconds=poll_interval,
    )
    return probe, summaries, children


def test_new_parts_reach_both_sinks_once() -> None:
    clock = _Clock()
    source = _FakeSource([_part("prt_1", 6_000), _part("prt_2", 7_000, kind="reasoning")])
    probe, summaries, children = _probe(source, clock)

    assert probe.poll() == 2
    assert children == ["ses_child", "ses_child"]
    assert summaries == [
        "tool_use:ralph_read_file [child:Sisyphus-Junior] Implement S7 history feed",
        "thinking: [child:Sisyphus-Junior] Implement S7 history feed",
    ]
    assert probe.observed_children == frozenset({"ses_child"})

    clock.now += 5.0
    assert probe.poll() == 0, "an already-forwarded part must not be forwarded twice"
    assert len(summaries) == 2


def test_high_water_mark_starts_at_invocation_wall_clock() -> None:
    clock = _Clock()
    source = _FakeSource([_part("prt_old", 4_000), _part("prt_new", 5_000)])
    probe, summaries, _ = _probe(source, clock)

    assert probe.poll() == 1
    assert source.calls == [("ses_parent", 5_000)]
    assert summaries[0].startswith("tool_use:ralph_read_file")


def test_polls_are_throttled_to_the_poll_interval() -> None:
    clock = _Clock()
    source = _FakeSource([])
    probe, _, _ = _probe(source, clock, poll_interval=2.0)

    probe.poll()
    clock.now += 1.0
    probe.poll()
    assert len(source.calls) == 1
    clock.now += 1.0
    probe.poll()
    assert len(source.calls) == 2


def test_no_parent_session_id_means_no_store_read() -> None:
    clock = _Clock()
    source = _FakeSource([_part("prt_1", 6_000)])
    probe, summaries, _ = _probe(source, clock, parent=None)

    assert probe.poll() == 0
    assert source.calls == []
    assert summaries == []


def test_a_re_updated_part_counts_as_new_work() -> None:
    clock = _Clock()
    source = _FakeSource([_part("prt_1", 6_000)])
    probe, summaries, _ = _probe(source, clock)
    assert probe.poll() == 1

    clock.now += 5.0
    source.parts = [_part("prt_1", 9_000)]
    assert probe.poll() == 1, "a streaming reasoning/tool part advances time_updated in place"
    assert len(summaries) == 2


def test_source_and_sink_failures_are_contained() -> None:
    clock = _Clock()

    class _Raising(_FakeSource):
        def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
            raise RuntimeError("store unreachable")

    probe, _, _ = _probe(_Raising([]), clock)
    assert probe.poll() == 0

    clock2 = _Clock()
    source = _FakeSource([_part("prt_1", 6_000)])

    def _boom(_: str) -> None:
        raise RuntimeError("sink broke")

    probe2 = OpenCodeSubagentSessionProbe(
        source=source,
        parent_session_id=lambda: "ses_parent",
        subagent_sink=_boom,
        child_progress_sink=_boom,
        monotonic=clock2.monotonic,
        wall_clock_ms=lambda: 0,
    )
    assert probe2.poll() == 1


def test_close_releases_the_source_and_stops_polling() -> None:
    clock = _Clock()
    source = _FakeSource([_part("prt_1", 6_000)])
    probe, _, _ = _probe(source, clock)

    probe.close()
    probe.close()
    assert source.closed is True
    assert probe.poll() == 0
    assert source.calls == []


def test_part_kind_and_summary_vocabulary() -> None:
    tool_part: dict[str, str] = {"type": "tool", "tool": "bash"}
    text_part: dict[str, str] = {"type": "text", "text": "hi"}
    step_part: dict[str, str] = {"type": "step-start"}
    not_an_object: list[int] = [1, 2]
    assert part_kind_from_data(json.dumps(tool_part)) == "tool:bash"
    assert part_kind_from_data(json.dumps(text_part)) == "text"
    assert part_kind_from_data(json.dumps(step_part)) == "step-start"
    assert part_kind_from_data("not json") == "part"
    assert part_kind_from_data(json.dumps(not_an_object)) == "part"
    anonymous = OpenCodeChildPart("ses_c", None, "", "prt", "text", 1)
    assert summarize_child_part(anonymous) == "text: [child]"


def test_default_db_path_honours_xdg_data_home(monkeypatch: object) -> None:
    from pytest import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    assert default_opencode_db_path() == Path("/xdg/data/opencode/opencode.db")
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", "/home/op")
    assert default_opencode_db_path() == Path("/home/op/.local/share/opencode/opencode.db")


def _build_store(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, title TEXT NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL
        );
        INSERT INTO session VALUES ('ses_parent', NULL, 'Sisyphus', 'parent');
        INSERT INTO session VALUES ('ses_child', 'ses_parent', 'Sisyphus-Junior', 'Fix gate');
        INSERT INTO session VALUES ('ses_other', 'ses_elsewhere', 'plan', 'unrelated');
        INSERT INTO part VALUES ('prt_a', 'msg_1', 'ses_child', 1000, 1000, '{"type":"text","text":"TASK"}');
        INSERT INTO part VALUES ('prt_b', 'msg_1', 'ses_child', 1000, 2500, '{"type":"tool","tool":"bash","callID":"c"}');
        INSERT INTO part VALUES ('prt_c', 'msg_2', 'ses_other', 3000, 3000, '{"type":"text"}');
        INSERT INTO part VALUES ('prt_d', 'msg_3', 'ses_parent', 3000, 3000, '{"type":"text"}');
        """
    )
    conn.commit()
    conn.close()


def test_sqlite_source_reads_children_of_the_parent_only(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    _build_store(db_path)
    source = SqliteOpenCodeChildPartSource(db_path)

    parts = source.fetch("ses_parent", 0)
    assert [(p.part_id, p.kind, p.time_updated_ms) for p in parts] == [
        ("prt_a", "text", 1000),
        ("prt_b", "tool:bash", 2500),
    ]
    assert parts[0].agent == "Sisyphus-Junior"
    assert parts[0].title == "Fix gate"

    assert [p.part_id for p in source.fetch("ses_parent", 2500)] == ["prt_b"]
    assert source.fetch("ses_elsewhere", 0)[0].child_session_id == "ses_other"
    source.close()


def test_sqlite_source_is_quiet_when_the_store_is_missing(tmp_path: Path) -> None:
    clock = _Clock()
    source = SqliteOpenCodeChildPartSource(tmp_path / "missing.db", monotonic=clock.monotonic)

    assert source.fetch("ses_parent", 0) == []
    _build_store(tmp_path / "missing.db")
    assert source.fetch("ses_parent", 0) == [], "reconnects are backed off, not retried per poll"
    clock.now += 60.0
    assert len(source.fetch("ses_parent", 0)) == 2
    source.close()
