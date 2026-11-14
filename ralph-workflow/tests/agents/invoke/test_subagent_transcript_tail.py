from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._pty_transcript import find_claude_subagent_transcripts
from ralph.agents.invoke._subagent_transcript import (
    SUBAGENT_DISPATCH_TOOLS,
    ClaudeSubagentTranscriptTails,
    R7AbsentLayoutDiagnostic,
    read_meta_file,
    summarize_event,
)
from ralph.agents.parsers.interactive_transcript_event import InteractiveTranscriptEvent

if TYPE_CHECKING:
    import pytest


_SINK_WAIT_TIMEOUT_SECONDS = 5.0


def _next_summary(delivered: queue.SimpleQueue[str], *, what: str) -> str:
    try:
        return delivered.get(timeout=_SINK_WAIT_TIMEOUT_SECONDS)
    except queue.Empty:
        raise AssertionError(f"the subagent sink never surfaced {what}") from None


def _setup_shadow_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    project_key: str,
    session_id: str,
) -> Path:
    shadow_home = tmp_path / "shadow-home"
    shadow_home.mkdir(parents=True, exist_ok=True)
    project_dir = shadow_home / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    (project_dir / session_id / "subagents").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{session_id}.jsonl").touch()
    monkeypatch.setattr(Path, "home", lambda: shadow_home)
    return project_dir


def _write_jsonl_line(path: Path, obj: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj) + "\n")


def _write_meta_file(path: Path, obj: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle)


def test_subagent_events_feed_record_subagent_work_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_1",
    )
    sub_path = project_dir / "sess_1" / "subagents" / "agent-ae8172f08ddb4f463.jsonl"
    _write_jsonl_line(
        sub_path,
        {
            "type": "assistant",
            "isSidechain": True,
            "agentId": "ae8172f08ddb4f463",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Read", "id": "tu_1"}],
            },
        },
    )
    _write_jsonl_line(
        sub_path,
        {
            "type": "assistant",
            "isSidechain": True,
            "agentId": "ae8172f08ddb4f463",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Grep", "id": "tu_2"}],
            },
        },
    )
    _write_jsonl_line(
        sub_path,
        {
            "type": "assistant",
            "isSidechain": True,
            "agentId": "ae8172f08ddb4f463",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Edit", "id": "tu_3"}],
            },
        },
    )

    sink_calls: list[str] = []
    delivered: queue.SimpleQueue[str] = queue.SimpleQueue()
    stop = threading.Event()

    def _record(summary: str) -> None:
        sink_calls.append(summary)
        delivered.put(summary)

    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_1",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=_record,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.start()
        _next_summary(delivered, what="the first subagent event")
    finally:
        stop.set()
        tails.stop()

    assert sink_calls, "subagent sink received no events"
    assert any("tool_use:Read" in call for call in sink_calls)
    assert any("tool_use:Grep" in call for call in sink_calls)
    assert any("tool_use:Edit" in call for call in sink_calls)


def test_silent_parent_and_silent_children_still_record_no_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_silent",
    )
    sub_path = project_dir / "sess_silent" / "subagents" / "agent-aa4510ad576b74f67.jsonl"
    _write_jsonl_line(
        sub_path,
        {
            "type": "user",
            "isSidechain": True,
            "agentId": "aa4510ad576b74f67",
            "message": {"role": "user", "content": [{"type": "text", "text": "initial prompt"}]},
        },
    )
    _write_jsonl_line(
        sub_path,
        {
            "type": "assistant",
            "isSidechain": True,
            "agentId": "aa4510ad576b74f67",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "planning the next step"}],
            },
        },
    )

    sink_calls: list[str] = []
    delivered: queue.SimpleQueue[str] = queue.SimpleQueue()
    stop = threading.Event()

    def _record(summary: str) -> None:
        sink_calls.append(summary)
        delivered.put(summary)

    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_silent",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=_record,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.start()
        _next_summary(delivered, what="the fixture's non-tool summary")
    finally:
        stop.set()
        tails.stop()

    tool_calls = [c for c in sink_calls if c.startswith(("tool_use:", "tool_result:"))]
    assert tool_calls == []


def test_r7_diagnostic_fires_when_subagents_dir_missing_for_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_r7",
    )
    sub_dir = project_dir / "sess_r7" / "subagents"
    for child in sub_dir.iterdir():
        child.unlink()
    sub_dir.rmdir()

    diagnostics: list[R7AbsentLayoutDiagnostic] = []
    stop = threading.Event()
    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_r7",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=lambda summary: None,
        r7_sink=diagnostics.append,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.note_parent_record({"version": "2.1.223"})
        tails.note_dispatch(tool_use_id="tu_dispatch_1", tool_name="Agent")
    finally:
        stop.set()
        tails.stop()

    assert len(diagnostics) == 1
    diag = diagnostics[0]
    assert diag.code == "R7_SUBAGENT_LAYOUT_MISSING"
    assert diag.claude_code_version == "2.1.223"
    assert diag.project_key == "-home-test-workspace"
    assert diag.session_id == "sess_r7"
    assert diag.probed_path.endswith("/.claude/projects/-home-test-workspace/sess_r7/subagents")
    assert diag.dispatch_tool_use_id == "tu_dispatch_1"
    assert diag.dispatch_tool_name == "Agent"


def test_r7_diagnostic_does_not_fire_when_subagents_dir_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_r7_ok",
    )
    sub_path = project_dir / "sess_r7_ok" / "subagents" / "agent-ae8172f08ddb4f463.jsonl"
    sub_path.touch()

    diagnostics: list[R7AbsentLayoutDiagnostic] = []
    stop = threading.Event()
    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_r7_ok",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=lambda summary: None,
        r7_sink=diagnostics.append,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.note_dispatch(tool_use_id="tu_dispatch_1", tool_name="Agent")
    finally:
        stop.set()
        tails.stop()

    assert diagnostics == []


def test_r7_diagnostic_does_not_fire_for_non_dispatch_tool_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_r7_bash",
    )
    sub_dir = project_dir / "sess_r7_bash" / "subagents"
    for child in sub_dir.iterdir():
        child.unlink()
    sub_dir.rmdir()

    diagnostics: list[R7AbsentLayoutDiagnostic] = []
    stop = threading.Event()
    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_r7_bash",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=lambda summary: None,
        r7_sink=diagnostics.append,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.note_dispatch(tool_use_id="tu_bash_1", tool_name="Bash")
    finally:
        stop.set()
        tails.stop()

    assert diagnostics == []


def test_r7_diagnostic_fires_for_each_distinct_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_r7_two",
    )
    sub_dir = project_dir / "sess_r7_two" / "subagents"
    for child in sub_dir.iterdir():
        child.unlink()
    sub_dir.rmdir()

    diagnostics: list[R7AbsentLayoutDiagnostic] = []
    stop = threading.Event()
    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_r7_two",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=lambda summary: None,
        r7_sink=diagnostics.append,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.note_dispatch(tool_use_id="tu_dispatch_A", tool_name="Agent")
        tails.note_dispatch(tool_use_id="tu_dispatch_B", tool_name="Agent")
    finally:
        stop.set()
        tails.stop()

    assert len(diagnostics) == 2
    assert {d.dispatch_tool_use_id for d in diagnostics} == {"tu_dispatch_A", "tu_dispatch_B"}


def test_tail_survives_quiet_then_active_child_and_drops_on_tool_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_lifecycle",
    )
    sub_path = project_dir / "sess_lifecycle" / "subagents" / "agent-aa4510ad576b74f67.jsonl"
    meta_path = sub_path.with_suffix(".meta.json")
    _write_meta_file(
        meta_path,
        {
            "agentType": "general-purpose",
            "description": "test child",
            "toolUseId": "tu_dispatch_1",
            "spawnDepth": 1,
        },
    )

    sink_calls: list[str] = []
    delivered: queue.SimpleQueue[str] = queue.SimpleQueue()
    stop = threading.Event()
    clock_value = [0.0]

    def _clock() -> float:
        return clock_value[0]

    def _record(summary: str) -> None:
        sink_calls.append(summary)
        delivered.put(summary)

    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_lifecycle",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=_record,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=_clock,
    )
    try:
        tails.note_parent_record({"version": "2.1.223"})
        tails.note_dispatch(tool_use_id="tu_dispatch_1", tool_name="Agent")
        tails.start()
        _write_jsonl_line(
            sub_path,
            {
                "type": "assistant",
                "isSidechain": True,
                "agentId": "aa4510ad576b74f67",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read", "id": "tu_1"}],
                },
            },
        )
        _next_summary(delivered, what="the first subagent event")
        first_event_count = len(sink_calls)
        assert first_event_count >= 1

        clock_value[0] = 200.0
        _write_jsonl_line(
            sub_path,
            {
                "type": "assistant",
                "isSidechain": True,
                "agentId": "aa4510ad576b74f67",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Edit", "id": "tu_2"}],
                },
            },
        )
        _next_summary(delivered, what="the second subagent event")
        assert len(sink_calls) >= first_event_count + 1, (
            f"second event was dropped; the tailer should still be"
            f" alive on the child file after a 200s silent window."
            f" sink_calls={sink_calls!r}"
        )
    finally:
        stop.set()
        tails.stop()


def test_child_event_summary_carries_meta_metadata() -> None:
    meta = {
        "agentType": "general-purpose",
        "description": "Resume and complete plan revision",
        "toolUseId": "toolu_01W6KdMFDkyGPNj431JA8wNW",
        "spawnDepth": 1,
    }
    event = InteractiveTranscriptEvent(
        kind="tool_use",
        text="claude tool: Read",
        metadata={"tool": "Read", "tool_use_id": "tu_1"},
    )
    summary = _invoke_summarize(event, meta, Path("/tmp/agent-aa4510ad576b74f67.jsonl"))
    assert summary == "tool_use:Read"


def _invoke_summarize(
    event: InteractiveTranscriptEvent,
    meta_dict: dict[str, object] | None,
    transcript_path: Path,
) -> str | None:
    return summarize_event(event, meta_dict, transcript_path)


def test_find_claude_subagent_transcripts_returns_listed_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_discover",
    )
    sub_dir = project_dir / "sess_discover" / "subagents"
    (sub_dir / "agent-bbb.jsonl").write_text("{}\n", encoding="utf-8")
    meta = sub_dir / "agent-bbb.meta.json"
    meta.write_text(json.dumps({"agentType": "general-purpose"}), encoding="utf-8")
    (sub_dir / "agent-aaa.jsonl").write_text("{}\n", encoding="utf-8")

    found = find_claude_subagent_transcripts("sess_discover")
    assert len(found) == 2
    names = sorted(path.name for path, _meta in found)
    assert names == ["agent-aaa.jsonl", "agent-bbb.jsonl"]
    by_name = {path.name: (path, meta) for path, meta in found}
    assert by_name["agent-bbb.jsonl"][1] == meta
    assert by_name["agent-aaa.jsonl"][1] is None


def test_read_meta_file_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert read_meta_file(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert read_meta_file(bad) is None
    not_dict = tmp_path / "not-dict.json"
    not_dict.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_meta_file(not_dict) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"agentType": "general-purpose"}), encoding="utf-8")
    assert read_meta_file(good) == {"agentType": "general-purpose"}


def test_subagent_dispatch_tools_set() -> None:
    assert frozenset({"Agent", "Task"}) == SUBAGENT_DISPATCH_TOOLS


def test_r7_diagnostic_carries_version_field_when_parent_record_present() -> None:
    stop = threading.Event()
    tails_with_version = ClaudeSubagentTranscriptTails(
        session_id="sess_x",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=lambda summary: None,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    tails_with_version.note_parent_record({"version": "2.1.223"})
    assert tails_with_version._claude_code_version == "2.1.223"

    stop2 = threading.Event()
    tails_no_version = ClaudeSubagentTranscriptTails(
        session_id="sess_y",
        project_key="-home-test-workspace",
        monitor_stop=stop2,
        subagent_sink=lambda summary: None,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    assert tails_no_version._claude_code_version is None


def test_note_completion_drops_child_matching_parent_tool_use_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_completion",
    )
    sub_path = project_dir / "sess_completion" / "subagents" / "agent-completed.jsonl"
    sub_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": True,
                "agentId": "completed",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read", "id": "tu_c1"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    meta_path = sub_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "agentType": "general-purpose",
                "description": "test child for completion",
                "toolUseId": "tu_dispatch_completed",
                "spawnDepth": 1,
            }
        ),
        encoding="utf-8",
    )
    stop = threading.Event()
    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_completion",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=lambda summary: None,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails._discover_new_files()
        assert len(tails._tails) == 1
        dropped = tails.note_completion(tool_use_id="tu_dispatch_completed")
        assert dropped is True
        assert len(tails._tails) == 0
        other_sub_path = project_dir / "sess_completion" / "subagents" / "agent-other.jsonl"
        other_sub_path.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": True,
                    "agentId": "other",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "Bash", "id": "tu_o1"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (other_sub_path.with_suffix(".meta.json")).write_text(
            json.dumps(
                {
                    "agentType": "general-purpose",
                    "description": "other child",
                    "toolUseId": "tu_other_dispatch",
                    "spawnDepth": 1,
                }
            ),
            encoding="utf-8",
        )
        tails._discover_new_files()
        assert len(tails._tails) == 1
        not_dropped = tails.note_completion(tool_use_id="tu_other_dispatch")
        assert not_dropped is True
        assert len(tails._tails) == 0
        third_sub_path = project_dir / "sess_completion" / "subagents" / "agent-third.jsonl"
        third_sub_path.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": True,
                    "agentId": "third",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "Read", "id": "tu_t1"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (third_sub_path.with_suffix(".meta.json")).write_text(
            json.dumps(
                {
                    "agentType": "general-purpose",
                    "description": "third child",
                    "toolUseId": "tu_third_dispatch",
                    "spawnDepth": 1,
                }
            ),
            encoding="utf-8",
        )
        tails._discover_new_files()
        assert len(tails._tails) == 1
        empty = tails.note_completion(tool_use_id="")
        assert empty is False
        assert len(tails._tails) == 1
    finally:
        stop.set()
        tails.stop()
