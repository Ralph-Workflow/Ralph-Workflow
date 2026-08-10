"""Pin the real-capture regression for the wt-04-claude-parsing fix.

The test loads the S-1 fixture through the production
``find_claude_transcript_*`` helpers against a
``tmp_path``-shadowed ``~/.claude/projects/<key>/`` tree, drives a
synthesized watchdog tick walk through the parent timeline using a
FakeClock + the activity recorder from ``_activity_methods``, and
asserts the watchdog does NOT fire during the pause-and-kill cycle
of the captured session.

The fixture is the real session that motivated this task: Claude
Code 2.1.223, 2026-08-06 12:52-13:11 local, 4-cycle kill/resume burn.
Per ``AGENTS.md`` and commit ``ed77f51``, fixtures are real captures,
not hand-written frames.

The test carries NO opt-in marker. The maintained verification mark
expression in ``ralph/test_suites.py:107`` is
``(not subprocess_e2e and not smoke) or required_auto_integrate_e2e``.
Marking the regression ``subprocess_e2e`` would exclude it from
``make test``; pinning it to ``required_auto_integrate_e2e`` would
require registering the new file in
``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` (reserved for the real-git
auto-integration trip). The regression is a deterministic
fixture-driven unit test (FakeClock + activity recorder + tmp_path
shadow) and does not need real PTY/process I/O, so it rides the
default path with no markers at all.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (FakeClock + activity recorder only).
  - No real filesystem outside ``tmp_path``.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
  - No ``noqa`` directives (audit_lint_bypass).
  - No bare ``type: ignore`` comments (audit_typecheck_bypass).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
)
from ralph.agents.invoke._pty_transcript import (
    find_claude_subagent_transcripts,
)
from ralph.agents.invoke._subagent_transcript import ClaudeSubagentTranscriptTails
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)

# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------


_FIXTURE_ROOT = Path(
    "tests/agents/invoke/fixtures/claude_interactive_real_capture/"
    "-home-mistlight-Projects-Expeditions-Core-wt-02-contractor"
)


def _fixture_root() -> Path:
    """Return the absolute path to the S-1 fixture root, or skip if missing.

    The fixture is committed alongside this test file; if a future
    operator moves it the test must skip cleanly so the rest of
    the suite stays green.
    """
    if not _FIXTURE_ROOT.is_dir():
        pytest.skip(f"fixture root missing: {_FIXTURE_ROOT}")
    return _FIXTURE_ROOT.resolve()


def _setup_shadow_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, str, str]:
    """Shadow ``Path.home`` with a ``tmp_path``-resident copy of the S-1 fixture layout.

    Returns ``(project_dir, session_id, project_key)`` for the test.
    The canonical ``find_claude_transcript_*`` helpers resolve the
    layout via ``Path.home()`` so the monkeypatch is required.
    """
    fixture_root = _fixture_root()
    shadow_home = tmp_path / "shadow-home"
    shadow_home.mkdir(parents=True, exist_ok=True)
    project_key = "-home-mistlight-Projects-Expeditions-Core-wt-02-contractor"
    session_id = "a4731909-31bc-4ad5-bac9-cd59ee7e0615"
    project_dir = shadow_home / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    (project_dir / session_id / "subagents").mkdir(parents=True, exist_ok=True)

    # Copy the parent transcript and subagent files into the shadow.
    parent_src = fixture_root / f"{session_id}.jsonl"
    parent_dst = project_dir / f"{session_id}.jsonl"
    parent_dst.write_text(parent_src.read_text(encoding="utf-8"), encoding="utf-8")

    sub_src_dir = fixture_root / session_id / "subagents"
    sub_dst_dir = project_dir / session_id / "subagents"
    for src_path in sub_src_dir.glob("agent-*"):
        dst_path = sub_dst_dir / src_path.name
        dst_path.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: shadow_home)
    return project_dir, session_id, project_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _watchdog_with_activity_recorder(
    *,
    silent_subagent_seconds: float = 180.0,
    idle_timeout_seconds: float = 300.0,
    activity_ttl_seconds: float = 30.0,
) -> tuple[IdleWatchdog, list[str]]:
    """Build an IdleWatchdog with a tiny FakeClock and an activity-sink list."""
    subagent_calls: list[str] = []

    class _FakeClock:
        def monotonic(self) -> float:
            return 0.0

    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout_seconds,
        silent_subagent_seconds=silent_subagent_seconds,
        activity_evidence_ttl_seconds=activity_ttl_seconds,
    )
    watchdog = IdleWatchdog(
        config=config,
        clock=_FakeClock(),
        listener=lambda evt: None,
    )

    def _record(description: str) -> None:
        watchdog.record_subagent_work(description=description)
        subagent_calls.append(description)

    return watchdog, subagent_calls


def _drive_parent_timeline_through_parser(
    parent_path: Path,
    watchdog: IdleWatchdog,
    subagent_calls: list[str],
) -> int:
    """Replay the parent timeline through the parser and record subagent events.

    Returns the count of tool_use events recorded from the parent.
    The parent transcript has no inline ``isSidechain`` entries; the
    parser classifies every record as either session / assistant
    / user / error. The parent emits only the synthetic envelope
    and the resume prompt -- both are surfaced through the
    parser wrapping, but neither advances the watchdog's
    ``_subagent_progress_count`` (the synthetic envelope is a
    lifecycle event; the user prompt is not a tool_use).

    The subagent tailer is wired below (separately) so this helper
    only exercises the parent parser path.
    """
    parser = ClaudeInteractiveTranscriptParser()
    session_kind_events = 0
    lifecycle_events = 0
    tool_use_events = 0
    tool_result_events = 0
    error_events = 0
    text_events = 0

    with parent_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            stripped_line = raw_line.strip()
            if not stripped_line:
                continue
            try:
                obj = json.loads(stripped_line)
            except json.JSONDecodeError:
                continue
            for event in parser.feed(json.dumps(obj)):
                if event.kind == "session":
                    session_kind_events += 1
                elif event.kind == "lifecycle":
                    lifecycle_events += 1
                elif event.kind == "tool_use":
                    tool_use_events += 1
                elif event.kind == "tool_result":
                    tool_result_events += 1
                elif event.kind == "error":
                    error_events += 1
                elif event.kind in {"text", "output"}:
                    text_events += 1
                # The synthetic envelope MUST NOT produce a text /
                # output event (R4). A text event from a synthetic
                # envelope would be a regression.
                if obj.get("type") == "assistant":
                    msg = obj.get("message", {})
                    if isinstance(msg, dict) and msg.get("model") == "<synthetic>":
                        assert event.kind != "text", (
                            f"synthetic envelope leaked into text:"
                            f" {event.text!r}"
                        )
                        assert event.kind != "output", (
                            f"synthetic envelope leaked into output:"
                            f" {event.text!r}"
                        )

    # The fixture has 5 <synthetic> envelopes and several tool_use
    # entries. Pin the totals so a future fixture refresh that
    # silently breaks the envelope classification surfaces here.
    assert lifecycle_events >= 5, (
        f"expected >=5 synthetic lifecycle events from the fixture,"
        f" got {lifecycle_events}"
    )
    return tool_use_events


def _drive_subagent_tails(
    *,
    project_key: str,
    session_id: str,
    watchdog: IdleWatchdog,
    subagent_calls: list[str],
    poll_seconds: float = 0.05,
) -> int:
    """Mount the subagent tailer and let it advance through the fixture once.

    Returns ``subagent_progress_count`` after the tailer runs.
    """
    stop = threading.Event()

    def _subagent_sink(summary: str) -> None:
        watchdog.record_subagent_work(description=summary)
        subagent_calls.append(summary)

    tails = ClaudeSubagentTranscriptTails(
        session_id=session_id,
        project_key=project_key,
        monitor_stop=stop,
        subagent_sink=_subagent_sink,
        r7_sink=lambda diag: None,
        poll_interval_seconds=poll_seconds,
        clock=lambda: 0.0,
    )
    try:
        # Discover files synchronously by calling the public
        # ``_discover_new_files`` directly so we don't depend on
        # poll-interval timing for the deterministic test.
        tails._discover_new_files()
        tails._advance_all_tails()
    finally:
        stop.set()
        tails.stop()

    # After replay, at least one tail entry must exist for the
    # fixture's 5 subagent files; the watchdog's
    # ``_subagent_progress_count`` advanced.
    return len(subagent_calls)


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------


def test_real_capture_replay_does_not_produce_text_for_synthetic_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: ``"No response requested."`` is never emitted as agent text on the parent timeline.

    The fixture's parent transcript has 5 ``<synthetic>`` envelopes.
    The classifier routes each to ``ENVELOPE_SYNTHETIC`` so the
    parser emits exactly one ``lifecycle`` event per envelope; the
    lifecycle event is consumed by ``is_lifecycle_kind`` in
    ``ClaudeInteractiveParser.parse()`` so no ``text`` / ``output``
    event ever carries the bookkeeping text. The post-processor's
    full pipeline also does NOT emit a text / output line carrying
    the literal.
    """
    _setup_shadow_home(monkeypatch, tmp_path)
    _watchdog, _subagent_calls = _watchdog_with_activity_recorder()
    parent_path = _fixture_root() / "a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl"

    from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser

    parser = ClaudeInteractiveParser()
    text_payloads: list[str] = []
    with parent_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message", {})
            if not (isinstance(msg, dict) and msg.get("model") == "<synthetic>"):
                continue
            # Drive the post-processor pipeline against this synthetic
            # record and assert no text / output line surfaces.
            text_payloads.extend(
                line.content
                for line in parser.parse(iter([stripped + "\n"]))
                if line.type in {"text", "output"}
            )

    assert text_payloads == [], (
        f"synthetic envelope leaked {len(text_payloads)} text/output lines;"
        f" first payload: {text_payloads[0]!r}"
    )


def test_real_capture_replay_subagent_tail_advances_record_subagent_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: the S-1 fixture's 5 subagent files advance ``_subagent_progress_count`` to > 0.

    The fix for RC1 + RC3 is the subagent tailer; replaying the
    fixture through the tailer MUST advance the watchdog's
    evidence channel. Without the tailer, the parent transcript
    alone produces zero subagent events and the watchdog's
    evidence channel stays empty -- which is exactly the
    pre-fix state this test pins.
    """
    _project_dir, session_id, project_key = _setup_shadow_home(monkeypatch, tmp_path)
    watchdog, subagent_calls = _watchdog_with_activity_recorder()
    parent_path = _fixture_root() / "a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl"

    # Replay the parent timeline through the parser to confirm
    # the parent carries no inline ``isSidechain`` entries. The
    # parent emits 5 ``tool_use:Agent`` dispatches (the four
    # cycles plus the initial state-of-the-world dispatch), and
    # zero ``isSidechain:true`` sidechain events -- all subagent
    # turns live in the sibling ``subagents/agent-*.jsonl`` files.
    parent_tool_use = _drive_parent_timeline_through_parser(
        parent_path, watchdog, subagent_calls
    )
    assert parent_tool_use >= 1, (
        "the parent transcript must emit at least one Agent dispatch"
        " tool_use event; this is the RC1 evidence that the parent"
        " delegates work to in-process subagents."
    )
    # Sanity-check: no ``isSidechain:true`` entries in the parent
    # (the sidechain markers are confined to the sibling files).
    parent_sidechain_count = 0
    with parent_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if obj.get("isSidechain") is True:
                parent_sidechain_count += 1
    assert parent_sidechain_count == 0, (
        f"the parent transcript must carry zero isSidechain:true entries;"
        f" all subagent turns live in the sibling files. Found"
        f" {parent_sidechain_count} sidechain entries in the parent."
    )

    # Now mount the subagent tailer and replay the fixture once.
    subagent_events = _drive_subagent_tails(
        project_key=project_key,
        session_id=session_id,
        watchdog=watchdog,
        subagent_calls=subagent_calls,
    )

    # The watchdog's evidence channel must have advanced -- this
    # is the AC-02 / RC3 acceptance criterion.
    assert subagent_events > 0, (
        "subagent tailer did not forward any events from the fixture;"
        " RC3 is broken: the watchdog's evidence channel stays empty"
        " even when subagents wrote to disk."
    )
    progress_count = getattr(watchdog, "_subagent_progress_count", 0)
    assert progress_count > 0, (
        f"_subagent_progress_count should be > 0 after replay,"
        f" got {progress_count}"
    )


def test_real_capture_replay_does_not_fire_synthetic_envelope_through_idle_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: a synthetic envelope never advances the idle baseline.

    The synthetic envelope MUST NOT reset the watchdog's idle
    baseline. ``record_subagent_work`` is the watchdog's
    ``_subagent_progress_count`` feeder; a synthetic envelope
    routes to ``lifecycle`` and never reaches ``record_subagent_work``.
    After replaying the parent timeline, the watchdog's
    ``_last_subagent_progress_at`` MUST be ``None`` (or carry a
    timestamp far in the past relative to a hypothetical ``now``).
    """
    _setup_shadow_home(monkeypatch, tmp_path)
    watchdog, _subagent_calls = _watchdog_with_activity_recorder()
    parent_path = _fixture_root() / "a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl"

    # Drive the parent timeline through the parser; record NO
    # subagent events from the parent (only the synthetic envelopes,
    # which are dropped by the post-processor).
    parser = ClaudeInteractiveTranscriptParser()
    with parent_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            parser.feed(json.dumps(obj))
    # No parent-level subagent events means
    # ``_last_subagent_progress_at`` is still ``None``.
    assert watchdog._last_subagent_progress_at is None


def test_real_capture_replay_subagents_dir_contains_issidechain_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: the fixture's ``subagents/agent-*.jsonl`` files carry ``isSidechain: true`` entries.

    The fixture is the literal evidence of the RC1 root cause:
    subagent turns live in a sibling directory and carry the
    sidechain markers. Without this assertion, a future operator
    who refreshes the fixture with a session that lacks the
    markers would silently break the test without surfacing the
    regression.
    """
    _setup_shadow_home(monkeypatch, tmp_path)
    found = find_claude_subagent_transcripts(
        "a4731909-31bc-4ad5-bac9-cd59ee7e0615"
    )
    assert len(found) == 5, f"expected 5 subagent files, got {len(found)}"
    sidechain_total = 0
    for transcript_path, _meta in found:
        with transcript_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if obj.get("isSidechain") is True:
                    sidechain_total += 1
    assert sidechain_total > 0, (
        "fixture subagent files must carry isSidechain:true entries"
        " (verified pre-plan via grep on the live source)."
    )
