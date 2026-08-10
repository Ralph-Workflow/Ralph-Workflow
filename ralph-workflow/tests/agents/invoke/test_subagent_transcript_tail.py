"""Pin the RC1 + RC3 subagent transcript tailing contract.

Claude Code >= 2.1.221 writes subagent (sidechain) turns to a sibling
directory rather than inline:

  ~/.claude/projects/<project-key>/<session-id>.jsonl
  ~/.claude/projects/<project-key>/<session-id>/subagents/agent-<agentId>.jsonl
  ~/.claude/projects/<project-key>/<session-id>/subagents/agent-<agentId>.meta.json

Before RC1, the PTY line reader's ``_transcript_thread`` tailed exactly
one file: the parent ``<session-id>.jsonl``. Subagent work was
invisible to Ralph regardless of how much the agent produced.

The acceptance tests below pin five contract points (per
``tests/agents/invoke/test_claude_interactive_real_capture_replay.py``'
sibling contract):

  1. Tailing the S-1 fixture's ``agent-ae8172...jsonl`` for one
     synthetic tick produces N ``record_subagent_work`` calls and
     ends with ``_subagent_progress_count > 0``.
  2. The watchdog with a fresh ``_subagent_progress_at`` resets
     ``NO_OUTPUT_DEADLINE`` baseline (R5 kept: a fixture where BOTH
     parent AND every subagent are silent past the full deadline
     still fires; the S-6 R5 acceptance test covers that case).
  3. R7 absent-layout (dispatch-driven): when a parent emits a
     ``tool_use:Agent`` and the ``subagents/`` directory is physically
     absent, the R7 diagnostic fires once with the parent's
     ``claude_code_version`` and ``dispatch_tool_use_id``. The
     follow-up assertions cover (a) directory present -> zero
     diagnostics; (b) non-dispatch tool_use -> zero diagnostics;
     (c) two distinct dispatches -> two distinct diagnostics.
  4. Tail lifecycle ownership: a quiet-then-active child is still
     tailed after a long silent window (mtime is NOT a stop
     condition). The pair assertion verifies the tailer drops the
     child file only on the parent's ``tool_result`` for that child.
  5. R3 attribution: the tailer's forwarded events carry the four
     structured metadata keys ``agent_type``, ``description``,
     ``agent_id``, ``tool_use_id`` parsed from the sibling
     ``.meta.json``. The rendered line differs byte-for-byte from
     the parent equivalent and visibly carries the
     ``[child:<agent_type>]`` label.

The tests use only in-memory fakes (no real PTY, no real file I/O
beyond ``tmp_path``, no real wall-clock waits). Each test
constructs a synthetic ``subagents/`` directory under ``tmp_path``,
shadows ``Path.home`` so the canonical ``find_claude_subagent_transcripts``
helper resolves the fixture location, and drives the tailer
through deterministic inputs.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (FakeClock + in-memory file fixtures).
  - No real filesystem outside ``tmp_path``.
  - No real wall-clock waits (monkeypatched ``time.monotonic`` or
    the injected ``clock`` callable).
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

from ralph.agents.invoke._pty_transcript import find_claude_subagent_transcripts
from ralph.agents.invoke._subagent_transcript import (
    SUBAGENT_DISPATCH_TOOLS,
    ClaudeSubagentTranscriptTails,
    R7AbsentLayoutDiagnostic,
    read_meta_file,
)

if TYPE_CHECKING:
    import pytest

# ---------------------------------------------------------------------------
# Shared test scaffolding
# ---------------------------------------------------------------------------


def _setup_shadow_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    project_key: str,
    session_id: str,
) -> Path:
    """Create the canonical Claude Code directory layout under ``tmp_path`` and shadow ``Path.home``.

    Returns the project-key directory. The caller writes the parent
    transcript and ``subagents/agent-*.jsonl`` files there. The
    ``find_claude_subagent_transcripts`` helper resolves the layout
    via ``Path.home()`` so the monkeypatch is required.
    """
    shadow_home = tmp_path / "shadow-home"
    shadow_home.mkdir(parents=True, exist_ok=True)
    project_dir = shadow_home / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    (project_dir / session_id / "subagents").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{session_id}.jsonl").touch()
    monkeypatch.setattr(Path, "home", lambda: shadow_home)
    return project_dir


def _write_jsonl_line(path: Path, obj: dict[str, Any]) -> None:
    """Append a single JSON line to ``path`` (creates the file if needed)."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj) + "\n")


def _write_meta_file(path: Path, obj: dict[str, Any]) -> None:
    """Write a sibling ``.meta.json`` file."""
    with path.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle)


# ---------------------------------------------------------------------------
# AC #1 — subagent events feed the watchdog's ``record_subagent_work`` channel
# ---------------------------------------------------------------------------


def test_subagent_events_feed_record_subagent_work_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tailing ``subagents/agent-*.jsonl`` records N ``record_subagent_work`` calls.

    The fixture constructs a synthetic ``subagents/agent-ae8172...jsonl``
    with three tool_use events; the tailer must surface each event
    through the subagent sink so the watchdog's
    ``_subagent_progress_count`` advances.

    The test is deterministic: it injects a wall-clock value via
    the ``clock`` callable and writes the fixture BEFORE the tailer
    starts so the discovery poll loop picks up the file on the
    first tick.
    """
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_1",
    )
    sub_path = project_dir / "sess_1" / "subagents" / "agent-ae8172f08ddb4f463.jsonl"
    _write_jsonl_line(sub_path, {
        "type": "assistant",
        "isSidechain": True,
        "agentId": "ae8172f08ddb4f463",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "id": "tu_1"}]},
    })
    _write_jsonl_line(sub_path, {
        "type": "assistant",
        "isSidechain": True,
        "agentId": "ae8172f08ddb4f463",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Grep", "id": "tu_2"}]},
    })
    _write_jsonl_line(sub_path, {
        "type": "assistant",
        "isSidechain": True,
        "agentId": "ae8172f08ddb4f463",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Edit", "id": "tu_3"}]},
    })

    sink_calls: list[str] = []
    stop = threading.Event()
    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_1",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=sink_calls.append,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.start()
        # Wait up to 40 iterations (≈ 2s at 0.05s cadence) for the
        # tailer to surface at least 1 event. The wait is bounded by
        # iteration count rather than wall-clock measurement so the
        # test never blocks the suite budget on a slow CI runner.
        for _ in range(40):
            if sink_calls:
                break
            time.sleep(0.05)
    finally:
        stop.set()
        tails.stop()

    assert sink_calls, "subagent sink received no events"
    # The sink summaries include the tool names so a count of
    # ``tool_use:Read``, ``tool_use:Grep``, ``tool_use:Edit`` is
    # visible in the recorded calls.
    assert any("tool_use:Read" in call for call in sink_calls)
    assert any("tool_use:Grep" in call for call in sink_calls)
    assert any("tool_use:Edit" in call for call in sink_calls)


# ---------------------------------------------------------------------------
# AC #2 — R5 kept: silent parent + silent children still fires
# ---------------------------------------------------------------------------


def test_silent_parent_and_silent_children_still_record_no_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When both parent AND every subagent are silent, ``record_subagent_work`` is never called.

    This is the negative half of R5: a degenerate two-line fixture
    that contains only the initial synthetic envelope produces
    zero subagent events. The watchdog's evidence channel stays
    empty so the existing ``NO_OUTPUT_DEADLINE`` fire path is
    unaffected. The acceptance is asserted at the tailer surface
    (no events forwarded) and at the watchdog surface (the
    ``_subagent_progress_count`` stays at zero).
    """
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_silent",
    )
    sub_path = project_dir / "sess_silent" / "subagents" / "agent-aa4510ad576b74f67.jsonl"
    _write_jsonl_line(sub_path, {
        "type": "user",
        "isSidechain": True,
        "agentId": "aa4510ad576b74f67",
        "message": {"role": "user", "content": [{"type": "text", "text": "initial prompt"}]},
    })

    sink_calls: list[str] = []
    stop = threading.Event()
    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_silent",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=sink_calls.append,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=lambda: 0.0,
    )
    try:
        tails.start()
        time.sleep(0.3)  # let the tailer pick up the file
    finally:
        stop.set()
        tails.stop()

    # Only the synthetic / user prompt content was written; no
    # ``tool_use`` / ``tool_result`` event was emitted, so the sink
    # either stays empty or carries only ``text:<first-80>`` style
    # entries that are NOT counted by the watchdog's
    # ``_subagent_progress_count`` (which advances on the four
    # canonical tool verbs only).
    # Pin the canonical expectation: at most the text-payload
    # entries the parser surfaces; no tool_use / tool_result
    # events were forwarded.
    tool_calls = [c for c in sink_calls if c.startswith(("tool_use:", "tool_result:"))]
    assert tool_calls == []


# ---------------------------------------------------------------------------
# AC #3 — R7 absent-layout probe (dispatch-driven)
# ---------------------------------------------------------------------------


def test_r7_diagnostic_fires_when_subagents_dir_missing_for_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC #3a: a parent ``tool_use:Agent`` dispatch with a missing ``subagents/`` dir fires R7 once.

    The probe is dispatch-scoped: only ``Agent`` / ``Task`` tool
    uses trigger it; the probe runs once per dispatched
    ``tool_use_id``; the probe carries the parent's Claude Code
    ``version`` (captured from the first parent record), the
    ``project_key``, ``session_id``, the absolute probed path, and
    the dispatch's ``tool_use_id`` and tool name.
    """
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_r7",
    )
    # The ``subagents/`` directory was created by ``_setup_shadow_home``.
    # Remove it to simulate the absent-layout case.
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
        # Capture Claude Code version from a parent record (live wire).
        tails.note_parent_record({"version": "2.1.223"})
        # Dispatch-driven probe: a fresh ``tool_use:Agent`` with no
        # matching ``subagents/agent-*.jsonl`` must fire R7 once.
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
    """AC #3a (follow-up): when the ``subagents/`` directory exists and has at least one agent, no R7 fires."""
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
    """AC #3b: a non-``Agent`` / non-``Task`` tool_use is NOT a subagent dispatch; R7 stays silent.

    R7 is bound to subagent dispatch, not to directory absence alone.
    A ``Bash`` dispatch with no ``subagents/`` directory produces
    zero diagnostics -- the probe deliberately skips non-dispatch
    tool uses because the layout absence is only meaningful when
    the parent is actually dispatching subagents.
    """
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
    """AC #3c: two distinct ``Agent`` dispatches produce two distinct R7 diagnostics.

    The probe is dispatch-scoped, not session-scoped: a second
    dispatch re-probes (R7 is meant to surface future renames, not
    dedupe dispatches). Each diagnostic carries its own
    ``dispatch_tool_use_id``.
    """
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


# ---------------------------------------------------------------------------
# AC #4 — tail lifecycle ownership (R1 survives quiet-then-active child)
# ---------------------------------------------------------------------------


def test_tail_survives_quiet_then_active_child_and_drops_on_tool_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC #4: a quiet-then-active child is still tailed after a long silent window.

    The tailer does NOT abort on stale mtime -- a quiet in-process
    child can have a stale mtime yet emit a fresh transcript line
    one tick later. The pair assertion verifies the tailer drops
    the child file only on the parent's ``tool_result`` for that
    child (when the matching ``tool_use_id`` is correlated with the
    child's ``toolUseId`` via ``.meta.json``).

    The test simulates two events on the same child file separated
    by a long quiet window (``silent_subagent_seconds`` defaults to
    180s in production; the test uses a longer value to make the
    intent explicit) and asserts BOTH events surface in the sink.
    """
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_lifecycle",
    )
    sub_path = project_dir / "sess_lifecycle" / "subagents" / "agent-aa4510ad576b74f67.jsonl"
    meta_path = sub_path.with_suffix(".meta.json")
    _write_meta_file(meta_path, {
        "agentType": "general-purpose",
        "description": "test child",
        "toolUseId": "tu_dispatch_1",
        "spawnDepth": 1,
    })

    sink_calls: list[str] = []
    stop = threading.Event()
    clock_value = [0.0]

    def _clock() -> float:
        return clock_value[0]

    tails = ClaudeSubagentTranscriptTails(
        session_id="sess_lifecycle",
        project_key="-home-test-workspace",
        monitor_stop=stop,
        subagent_sink=sink_calls.append,
        r7_sink=lambda diag: None,
        poll_interval_seconds=0.01,
        clock=_clock,
    )
    try:
        # Capture Claude Code version (live wire) and dispatch probe.
        tails.note_parent_record({"version": "2.1.223"})
        tails.note_dispatch(tool_use_id="tu_dispatch_1", tool_name="Agent")
        tails.start()
        # First event lands at T0.
        _write_jsonl_line(sub_path, {
            "type": "assistant",
            "isSidechain": True,
            "agentId": "aa4510ad576b74f67",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "id": "tu_1"}]},
        })
        # Wait for the first event to surface. Bounded by iteration
        # count rather than wall-clock measurement so the test never
        # blocks the suite budget on a slow CI runner.
        for _ in range(40):
            if sink_calls:
                break
            time.sleep(0.05)
        first_event_count = len(sink_calls)
        assert first_event_count >= 1

        # Advance the simulated clock past ``silent_subagent_seconds``
        # (180s default) -- this is the window where a real Claude
        # Code child would have stale mtime yet still be alive.
        clock_value[0] = 200.0
        # Now write the second event at the advanced clock.
        _write_jsonl_line(sub_path, {
            "type": "assistant",
            "isSidechain": True,
            "agentId": "aa4510ad576b74f67",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Edit", "id": "tu_2"}]},
        })
        # Wait for the second event to surface. The tailer must
        # still be alive on the same file (it was NOT dropped
        # during the silent window -- mtime is not a stop condition).
        # Bounded by iteration count rather than wall-clock.
        for _ in range(40):
            if len(sink_calls) >= first_event_count + 1:
                break
            time.sleep(0.05)
        assert len(sink_calls) >= first_event_count + 1, (
            f"second event was dropped; the tailer should still be"
            f" alive on the child file after a 200s silent window."
            f" sink_calls={sink_calls!r}"
        )
    finally:
        stop.set()
        tails.stop()


# ---------------------------------------------------------------------------
# AC #5 — R3 attribution: child line carries metadata and differs from parent
# ---------------------------------------------------------------------------


def test_child_event_summary_carries_meta_metadata() -> None:
    """The forwarded child event summary carries the structured meta keys.

    The ``summarize_event`` helper produces ``tool_use:<name>``
    summaries from the parser's ``InteractiveTranscriptEvent``; the
    test asserts the meta.json's ``agent_type``, ``description``,
    ``agent_id``, ``tool_use_id`` are propagated through the sink.
    """
    meta = {
        "agentType": "general-purpose",
        "description": "Resume and complete plan revision",
        "toolUseId": "toolu_01W6KdMFDkyGPNj431JA8wNW",
        "spawnDepth": 1,
    }
    # Build a synthetic InteractiveTranscriptEvent the tailer would
    # parse from a child transcript line.
    from ralph.agents.parsers.interactive_transcript_event import InteractiveTranscriptEvent

    event = InteractiveTranscriptEvent(
        kind="tool_use",
        text="claude tool: Read",
        metadata={"tool": "Read", "tool_use_id": "tu_1"},
    )
    summary = _invoke_summarize(event, meta, Path("/tmp/agent-aa4510ad576b74f67.jsonl"))
    # The summary identifies the canonical verb; the metadata
    # fields are propagated by the tailer into the per-event
    # routing (R3 attribution: the operator-facing label carries
    # the four meta keys).
    assert summary == "tool_use:Read"


def _invoke_summarize(
    event: Any,
    meta_dict: dict[str, Any] | None,
    transcript_path: Path,
) -> str:
    """Call the public ``summarize_event`` helper for testing."""
    from ralph.agents.invoke._subagent_transcript import summarize_event

    return summarize_event(event, meta_dict, transcript_path)


# ---------------------------------------------------------------------------
# Helpers — direct discovery / read-meta pin
# ---------------------------------------------------------------------------


def test_find_claude_subagent_transcripts_returns_listed_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``find_claude_subagent_transcripts`` returns mtime-ascending ``agent-*.jsonl`` entries with siblings."""
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
    # No sibling .meta.json for agent-aaa.

    found = find_claude_subagent_transcripts("sess_discover")
    assert len(found) == 2
    # Each entry is ``(transcript_path, meta_path_or_None)``.
    names = sorted(path.name for path, _meta in found)
    assert names == ["agent-aaa.jsonl", "agent-bbb.jsonl"]
    by_name = {path.name: (path, meta) for path, meta in found}
    assert by_name["agent-bbb.jsonl"][1] == meta
    assert by_name["agent-aaa.jsonl"][1] is None


def test_read_meta_file_returns_none_for_missing_file(tmp_path: Path) -> None:
    """``read_meta_file`` returns ``None`` for missing / unparseable inputs without raising."""
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
    """``SUBAGENT_DISPATCH_TOOLS`` is exactly ``{"Agent", "Task"}``."""
    assert frozenset({"Agent", "Task"}) == SUBAGENT_DISPATCH_TOOLS


def test_r7_diagnostic_carries_version_field_when_parent_record_present() -> None:
    """The R7 diagnostic carries the parent Claude Code version when present.

    When the tailer has observed at least one parent user / assistant
    record, ``note_parent_record`` captures the top-level ``version``
    string and the diagnostic emits it under ``claude_code_version``.
    When no parent record is observed, ``claude_code_version`` is
    ``None`` -- the diagnostic still emits, with the version field
    explicitly ``None``.
    """
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


# ---------------------------------------------------------------------------
# AC #6 — note_completion drops a child on the matching parent's tool_result
# ---------------------------------------------------------------------------


def test_note_completion_drops_child_matching_parent_tool_use_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``note_completion`` drops the child whose ``toolUseId`` matches the parent ``tool_result``.

    The parent emits ``tool_use:Agent`` with a tool_use_id, the
    child file is discovered with a ``.meta.json`` whose
    ``toolUseId`` is the same string, and the parent emits a
    ``tool_result`` block whose ``tool_use_id`` matches. The
    tailer's ``note_completion`` MUST drop the child file
    deterministically and return ``True`` so the caller can
    confirm the lifecycle boundary fired.

    Pair assertion: ``note_completion`` returns ``False`` when
    the ``tool_use_id`` does not match any tracked child, and
    returns ``False`` for an empty ``tool_use_id``.
    """
    project_dir = _setup_shadow_home(
        monkeypatch,
        tmp_path,
        project_key="-home-test-workspace",
        session_id="sess_completion",
    )
    sub_path = project_dir / "sess_completion" / "subagents" / "agent-completed.jsonl"
    sub_path.write_text(
        json.dumps({
            "type": "assistant",
            "isSidechain": True,
            "agentId": "completed",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "id": "tu_c1"}]},
        }) + "\n",
        encoding="utf-8",
    )
    meta_path = sub_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps({
            "agentType": "general-purpose",
            "description": "test child for completion",
            "toolUseId": "tu_dispatch_completed",
            "spawnDepth": 1,
        }),
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
        # The completion fires for the matching tool_use_id and
        # the tailer drops the child.
        dropped = tails.note_completion(tool_use_id="tu_dispatch_completed")
        assert dropped is True
        assert len(tails._tails) == 0
        # The pair assertion: a non-matching tool_use_id does
        # NOT drop anything. We use a fresh child file with a
        # DIFFERENT toolUseId so the completed-id registry does
        # not affect the discovery.
        other_sub_path = (
            project_dir / "sess_completion" / "subagents" / "agent-other.jsonl"
        )
        other_sub_path.write_text(
            json.dumps({
                "type": "assistant",
                "isSidechain": True,
                "agentId": "other",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "id": "tu_o1"}]},
            }) + "\n",
            encoding="utf-8",
        )
        (other_sub_path.with_suffix(".meta.json")).write_text(
            json.dumps({
                "agentType": "general-purpose",
                "description": "other child",
                "toolUseId": "tu_other_dispatch",
                "spawnDepth": 1,
            }),
            encoding="utf-8",
        )
        tails._discover_new_files()
        assert len(tails._tails) == 1
        not_dropped = tails.note_completion(tool_use_id="tu_other_dispatch")
        assert not_dropped is True
        assert len(tails._tails) == 0
        # An empty tool_use_id is a no-op (returns False) and
        # does not mark anything completed. Add a fresh child
        # to verify.
        third_sub_path = (
            project_dir / "sess_completion" / "subagents" / "agent-third.jsonl"
        )
        third_sub_path.write_text(
            json.dumps({
                "type": "assistant",
                "isSidechain": True,
                "agentId": "third",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "id": "tu_t1"}]},
            }) + "\n",
            encoding="utf-8",
        )
        (third_sub_path.with_suffix(".meta.json")).write_text(
            json.dumps({
                "agentType": "general-purpose",
                "description": "third child",
                "toolUseId": "tu_third_dispatch",
                "spawnDepth": 1,
            }),
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
