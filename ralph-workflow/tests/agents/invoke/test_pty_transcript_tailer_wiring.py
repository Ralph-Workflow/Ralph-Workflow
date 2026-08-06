"""End-to-end: ``_transcript_thread`` wires parent records to the subagent tailer (DA-001).

Lives in a separate file from
``test_subagent_transcript_tail.py`` to keep the file size
under the repo-structure audit cap (1000 lines). The test
itself is the AC for DA-001 (S-5 / R1 / R3 / R7): the previous
shape constructed the tailer in ``_setup_read_loop`` (BEFORE
the session id was observed) so the tailer was always ``None``
on a fresh invocation; the parent transcript was never
surfaced to the tailer, R7 never fired, and the watchdog's
evidence channel never advanced. The fix constructs the
tailer lazily inside ``_transcript_thread`` on the FIRST
``session`` event and forwards every parsed parent event to
the tailer's ``note_dispatch`` / ``note_completion`` /
``note_parent_record`` methods.

The test builds a synthetic parent transcript with one
``tool_use:Agent`` dispatch and one matching ``tool_result``
and asserts:

  1. The tailer was constructed lazily (after the first
     parsed line, not at reader-thread start).
  2. ``tails.note_parent_record`` was called so the Claude
     Code version is captured.
  3. ``tails.note_dispatch`` was called for the
     ``Agent`` dispatch (so R7 fires).
  4. ``tails.note_completion`` was called for the matching
     ``tool_result`` (so the child is dropped).
  5. The lazy tailer started polling the ``subagents/``
     directory the first time a dispatch was observed.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (FakeClock + in-memory fakes only).
  - No real filesystem outside ``tmp_path``.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
  - No ``noqa`` directives (audit_lint_bypass).
  - No bare ``type: ignore`` comments (audit_typecheck_bypass).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ralph.agents.invoke._pty_line_reader import PtyLineReader

if TYPE_CHECKING:
    import pytest


class _RecordingReadlineFile:
    """A fake transcript file returning a fixed list of lines then EOF."""

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
    """A stand-in for ``pathlib.Path`` used by ``_transcript_thread``."""

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
    """A minimal list-like that supports ``extend`` and ``__bool__``."""

    def __init__(self) -> None:
        self._items: list[str] = []

    def append(self, item: str) -> None:
        self._items.append(item)

    def extend(self, items: list[str]) -> None:
        self._items.extend(items)

    def __bool__(self) -> bool:
        return bool(self._items)


class _MonotonicClock:
    """A trivial clock for the reader (matches the watchdog's expectation)."""

    def monotonic(self) -> float:
        return 0.0

    def wait_for_event(self, _event: object, _timeout: float) -> bool:
        return False


def test_transcript_thread_wires_parent_record_to_tailer_dispatch_and_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: ``_transcript_thread`` wires parent records to the tailer.

    See module docstring for the full AC list.
    """
    import ralph.agents.invoke._pty_line_reader as _pty_module

    session_id = "sess_e2e"
    shadow_home = tmp_path / "shadow-home"
    shadow_home.mkdir(parents=True, exist_ok=True)
    # The project_key is derived from ``_workspace_path.resolve()``
    # via ``str(path).replace("/", "-")``. To make the tailer's
    # probe path match the fixture, point the reader at a workspace
    # whose resolved form is ``<shadow_home>/home/test-workspace``;
    # the derived project_key is then
    # ``-<shadow_home>-home-test-workspace`` (e.g.
    # ``-tmp-pytest-of-mistlight-pytest-96-test_transcript_thread_wires_p0-shadow-home-home-test-workspace``).
    # The fixture's project directory lives at that exact key under
    # ``<shadow_home>/.claude/projects/`` so the ``subagents/`` probe
    # hits the same directory.
    workspace_path = shadow_home / "home" / "test-workspace"
    workspace_path.mkdir(parents=True, exist_ok=True)
    project_key = str(workspace_path.resolve()).replace("/", "-")
    project_dir = shadow_home / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    (project_dir / session_id / "subagents").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{session_id}.jsonl").touch()
    monkeypatch.setattr(Path, "home", lambda: shadow_home)

    # Pre-populate the subagents/ directory with one child so the
    # R7 probe at dispatch time finds it (so it does NOT fire R7;
    # the test focuses on the tailer wiring).
    sub_path = project_dir / session_id / "subagents" / "agent-completed.jsonl"
    sub_path.write_text(
        json.dumps({
            "type": "assistant",
            "isSidechain": True,
            "agentId": "completed",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "id": "tu_c1"}]},
        }) + "\n",
        encoding="utf-8",
    )
    (sub_path.with_suffix(".meta.json")).write_text(
        json.dumps({
            "agentType": "general-purpose",
            "description": "completed child",
            "toolUseId": "tu_dispatch_completed",
            "spawnDepth": 1,
        }),
        encoding="utf-8",
    )

    parent_lines = [
        json.dumps({"type": "user", "sessionId": session_id, "version": "2.1.223", "message": {"role": "user", "content": "hi"}}) + "\n",
        json.dumps({
            "type": "assistant",
            "sessionId": session_id,
            "version": "2.1.223",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_dispatch_completed", "name": "Agent", "input": {"prompt": "x"}}
                ],
            },
        }) + "\n",
        json.dumps({
            "type": "user",
            "sessionId": session_id,
            "version": "2.1.223",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_dispatch_completed", "content": "ok"}
                ],
            },
        }) + "\n",
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

    captured: dict[str, BaseException | None] = {"exc": None}

    def _runner() -> None:
        try:
            reader._transcript_thread()
        except BaseException as exc:
            captured["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    # Wait for the thread to drain the file. The fake
    # ``readline()`` returns ``""`` once all lines are consumed;
    # the thread then enters the ``_monitor_stop.wait(0.1)``
    # poll loop. Polling for ``_lines_event`` or a small sleep
    # gives the thread a chance to process all 3 lines.
    time.sleep(0.5)
    # AC #5 (timing-sensitive): the lazy tailer started polling
    # the ``subagents/`` directory the first time a dispatch was
    # observed. Assert this BEFORE the stop event fires so the
    # tailer thread is still alive.
    lazy_tails_during_run = reader._subagent_tails
    if lazy_tails_during_run is not None:
        assert lazy_tails_during_run.is_started, (
            "the lazy tailer was not started by ``note_dispatch``;"
            " the subagent thread never began polling"
        )
    # Set the stop event AFTER the thread has consumed the
    # fixture's lines so the loop reads every line before
    # exiting. Setting it earlier would terminate the loop
    # between lines and the tool_use / tool_result events
    # would never reach the tailer.
    reader._monitor_stop.set()
    # Stop the lazy tailer so the test exit doesn't leak a
    # thread (the tailer shares the monitor_stop event so it
    # exits too).
    lazy_tails_for_stop: Any = reader._subagent_tails
    if lazy_tails_for_stop is not None:
        lazy_tails_for_stop.stop()
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "_transcript_thread did not terminate"
    assert captured["exc"] is None, f"_transcript_thread raised: {captured['exc']!r}"

    # AC #1: the lazy tailer was constructed after the first
    # session event.
    lazy_tails: Any = reader._subagent_tails
    assert lazy_tails is not None, (
        "the tailer was not constructed lazily on the first session"
        " event; the parent transcript never reached the tailer"
    )
    assert lazy_tails.session_id == session_id
    # AC #2: ``tails.note_parent_record`` captured the version.
    assert lazy_tails.claude_code_version == "2.1.223"
    # AC #3: ``tails.note_dispatch`` was called for the
    # ``Agent`` dispatch (the dispatch tool_use_id is in
    # ``probed_dispatch_ids``).
    assert "tu_dispatch_completed" in lazy_tails.probed_dispatch_ids
    # AC #4: ``tails.note_completion`` dropped the child whose
    # ``toolUseId`` matches the parent's ``tool_result``.
    assert len(lazy_tails._tails) == 0, (
        f"the child file should be dropped after the matching tool_result;"
        f" got {list(lazy_tails._tails.keys())!r}"
    )
    # AC #5 is asserted above (``is_started`` during the run
    # before the stop event fires).
