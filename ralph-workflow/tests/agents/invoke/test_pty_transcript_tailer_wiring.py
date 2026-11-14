from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._subagent_transcript import ClaudeSubagentTranscriptTails

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

    from ralph.agents.parsers.claude_interactive_transcript_parser import (
        ClaudeInteractiveTranscriptParser,
    )


class _RecordingReadlineFile:
    def __init__(self, lines: tuple[str, ...]) -> None:
        self._lines = list(lines)
        self.close_called = 0

    def readline(self) -> str:
        if not self._lines:
            return ""
        return self._lines.pop(0)

    def close(self) -> None:
        self.close_called += 1


class _FakeTranscriptPath:
    def __init__(self, fake_file: object) -> None:
        self._fake_file = fake_file

    def open(self, *_args: object, **_kwargs: object) -> object:
        return self._fake_file

    def __eq__(self, other: object) -> bool:
        return self is other

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return id(self)


class _BoundedList:
    def __init__(self) -> None:
        self._items: list[str] = []

    def append(self, item: str) -> None:
        self._items.append(item)

    def extend(self, items: list[str]) -> None:
        self._items.extend(items)

    def __bool__(self) -> bool:
        return bool(self._items)


class _MonotonicClock:
    def monotonic(self) -> float:
        return 0.0

    def wait_for_event(self, _event: object, _timeout: float) -> bool:
        return False


def _wait_for_tailer_start(event: threading.Event, reader: PtyLineReader) -> None:
    assert event.wait(timeout=1.0), "the lazy tailer did not start on Agent dispatch"
    tails = reader._subagent_tails
    assert tails is not None
    assert tails.is_started


def _install_tailer_start_signal(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType
) -> threading.Event:
    event = threading.Event()
    original_start = module.ClaudeSubagentTranscriptTails.start

    def start_and_signal(tails: ClaudeSubagentTranscriptTails) -> threading.Thread:
        thread = original_start(tails)
        event.set()
        return thread

    monkeypatch.setattr(module.ClaudeSubagentTranscriptTails, "start", start_and_signal)
    return event


def test_transcript_thread_wires_parent_record_to_tailer_dispatch_and_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import ralph.agents.invoke._pty_line_reader as _pty_module

    session_id = "sess_e2e"
    shadow_home = tmp_path / "shadow-home"
    shadow_home.mkdir(parents=True, exist_ok=True)
    workspace_path = shadow_home / "home" / "test-workspace"
    workspace_path.mkdir(parents=True, exist_ok=True)
    project_key = str(workspace_path.resolve()).replace("/", "-")
    project_dir = shadow_home / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    (project_dir / session_id / "subagents").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{session_id}.jsonl").touch()
    monkeypatch.setattr(Path, "home", lambda: shadow_home)

    sub_path = project_dir / session_id / "subagents" / "agent-completed.jsonl"
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
    (sub_path.with_suffix(".meta.json")).write_text(
        json.dumps(
            {
                "agentType": "general-purpose",
                "description": "completed child",
                "toolUseId": "tu_dispatch_completed",
                "spawnDepth": 1,
            }
        ),
        encoding="utf-8",
    )

    parent_lines = [
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "version": "2.1.223",
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        json.dumps(
            {
                "type": "assistant",
                "sessionId": session_id,
                "version": "2.1.223",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_dispatch_completed",
                            "name": "Agent",
                            "input": {"prompt": "x"},
                        }
                    ],
                },
            }
        )
        + "\n",
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "version": "2.1.223",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_dispatch_completed",
                            "content": "ok",
                        }
                    ],
                },
            }
        )
        + "\n",
    ]

    fake_file = _RecordingReadlineFile(tuple(parent_lines))
    fake_path = _FakeTranscriptPath(fake_file)

    def _record_open_call(_candidates: object) -> tuple[object, str] | None:
        return fake_path, session_id

    monkeypatch.setattr(_pty_module, "find_claude_transcript_entry", _record_open_call)

    reader = object.__new__(PtyLineReader)
    reader._monitor_stop = threading.Event()
    reader._workspace_path = workspace_path
    reader._expected_session_id = None
    reader._started_at_wall_clock = 0.0
    reader._transcript_session_ids = [session_id]
    reader._transcript_session_ids_lock = threading.Lock()
    reader._lines_lock = threading.Lock()
    reader._lines_queue = _BoundedList()
    reader._lines_event = threading.Event()
    reader._captured_session_id = None
    reader._subagent_tails = None
    reader._clock = _MonotonicClock()
    tailer_started = _install_tailer_start_signal(monkeypatch, _pty_module)

    captured: dict[str, BaseException | None] = {"exc": None}

    def _runner() -> None:
        try:
            reader._transcript_thread()
        except BaseException as exc:
            captured["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    _wait_for_tailer_start(tailer_started, reader)
    reader._monitor_stop.set()
    lazy_tails_for_stop: Any = reader._subagent_tails
    if lazy_tails_for_stop is not None:
        lazy_tails_for_stop.stop()
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "_transcript_thread did not terminate"
    assert captured["exc"] is None, f"_transcript_thread raised: {captured['exc']!r}"

    lazy_tails: Any = reader._subagent_tails
    assert lazy_tails is not None, (
        "the tailer was not constructed lazily on the first session"
        " event; the parent transcript never reached the tailer"
    )
    assert lazy_tails.session_id == session_id
    assert lazy_tails.claude_code_version == "2.1.223"
    assert "tu_dispatch_completed" in lazy_tails.probed_dispatch_ids
    assert len(lazy_tails._tails) == 0, (
        f"the child file should be dropped after the matching tool_result;"
        f" got {list(lazy_tails._tails.keys())!r}"
    )


def test_transcript_tailer_regression_completion_does_not_resurrect_prepopulated_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_id = "sess_completed"
    shadow_home = tmp_path / "shadow-home"
    workspace_path = shadow_home / "home" / "test-workspace"
    workspace_path.mkdir(parents=True)
    project_key = str(workspace_path.resolve()).replace("/", "-")
    project_dir = shadow_home / ".claude" / "projects" / project_key
    subagents_dir = project_dir / session_id / "subagents"
    subagents_dir.mkdir(parents=True)
    (project_dir / f"{session_id}.jsonl").touch()
    child_path = subagents_dir / "agent-completed.jsonl"
    child_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read", "id": "child-read"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    child_path.with_suffix(".meta.json").write_text(
        json.dumps({"toolUseId": "parent-dispatch"}), encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", lambda: shadow_home)

    monitor_stop = threading.Event()
    advance_started = threading.Event()
    allow_advance_to_finish = threading.Event()
    tailer = ClaudeSubagentTranscriptTails(
        session_id=session_id,
        project_key=project_key,
        monitor_stop=monitor_stop,
        subagent_sink=lambda _summary: None,
        r7_sink=lambda _diagnostic: None,
    )
    tailer._discover_new_files()
    assert str(child_path) in tailer._tails

    def block_forward(
        _parser: ClaudeInteractiveTranscriptParser,
        _chunk: str,
        _transcript_path: Path,
        _meta_dict: dict[str, object] | None,
    ) -> None:
        advance_started.set()
        assert allow_advance_to_finish.wait(timeout=1.0)

    monkeypatch.setattr(tailer, "_forward_chunk", block_forward)
    advance_thread = threading.Thread(target=tailer._advance_all_tails)
    advance_thread.start()
    assert advance_started.wait(timeout=1.0)

    completion_result: list[bool] = []
    completion_started = threading.Event()
    completion_finished = threading.Event()

    def complete_child() -> None:
        completion_started.set()
        completion_result.append(tailer.note_completion(tool_use_id="parent-dispatch"))
        completion_finished.set()

    completion_thread = threading.Thread(target=complete_child)
    completion_thread.start()
    assert completion_started.wait(timeout=1.0)
    assert not completion_finished.is_set()
    allow_advance_to_finish.set()
    advance_thread.join(timeout=1.0)
    completion_thread.join(timeout=1.0)

    assert not advance_thread.is_alive()
    assert not completion_thread.is_alive()
    assert completion_result == [True]
    assert str(child_path) not in tailer._tails
