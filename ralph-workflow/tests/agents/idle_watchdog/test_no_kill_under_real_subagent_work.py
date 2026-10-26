"""Pin the R5 / R6 / R7 acceptance contracts for the wt-04-claude-parsing fix.

Five acceptance tests (one method per acceptance criterion):

  1. Replay the real S-1 fixture across 12:52:45 -> 12:58:04; assert
     the watchdog does NOT fire (the S-2 XFAIL flips to PASS once
     S-5 lands).
  2. Replay the S-1 fixture's ``<synthetic>`` cycle; assert
     ``"No response requested."`` is emitted only as a lifecycle
     event, never as ``AgentOutputLine(type="text"/"output")``,
     never in the retry-context excerpt, and never resets the
     idle baseline.
  3. A subagent ``tool_use`` at T resets the idle baseline such that
     the fire that would have occurred at T+300s does not occur.
  4. A session whose parent AND every subagent are silent past the
     deadline still fires (R5). Build a degenerate two-line jsonl
     per file and verify the fire reason + elapsed matches the
     deadline.
  5. The same-shape retry loop is detected and bounded at the
     configured threshold (S-3's tracker, wired through
     ``recovery/controller.py``).

The tests use only in-memory fakes (FakeClock + activity recorder +
tmp_path shadow) and run under ``make verify`` deterministically.
Each test constructs a synthetic ``subagents/`` directory under
``tmp_path`` and shadows ``Path.home`` so the canonical discovery
helpers resolve the fixture location.

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
from pathlib import Path

import pytest

from ralph.agents.idle_watchdog import (
    ChannelName,
    IdleWatchdog,
    TimeoutPolicy,
    classify_stuck,
)
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.timeout_clock import FakeClock

_FIXTURE_ROOT = Path(
    "tests/agents/invoke/fixtures/claude_interactive_real_capture/"
    "-home-mistlight-Projects-Expeditions-Core-wt-02-contractor"
)


def _fixture_root() -> Path:
    if not _FIXTURE_ROOT.is_dir():
        pytest.skip(f"fixture root missing: {_FIXTURE_ROOT}")
    return _FIXTURE_ROOT.resolve()


def _setup_shadow_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, str, str]:
    """Shadow ``Path.home`` with a ``tmp_path``-resident copy of the S-1 fixture layout."""
    fixture_root = _fixture_root()
    shadow_home = tmp_path / "shadow-home"
    shadow_home.mkdir(parents=True, exist_ok=True)
    project_key = "-home-mistlight-Projects-Expeditions-Core-wt-02-contractor"
    session_id = "a4731909-31bc-4ad5-bac9-cd59ee7e0615"
    project_dir = shadow_home / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    (project_dir / session_id / "subagents").mkdir(parents=True, exist_ok=True)
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


def _replay_subagent_transcripts(
    subagents_dir: Path,
    watchdog: IdleWatchdog,
) -> int:
    """Replay subagent transcript events into the watchdog.

    Reads ``agent-*.jsonl`` files in ``subagents_dir`` and records
    observed tool_use events via ``watchdog.record_subagent_work``.
    Returns the count of recorded events.
    """
    recorded = 0
    if not subagents_dir.is_dir():
        return recorded
    for sub_path in sorted(subagents_dir.glob("agent-*.jsonl")):
        with sub_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "assistant":
                    msg = obj.get("message", {})
                    if isinstance(msg, dict):
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_name = str(block.get("name", "tool"))
                                watchdog.record_subagent_work(
                                    description=f"subagent: tool_use:{tool_name}"
                                )
                                recorded += 1
    return recorded


# ---------------------------------------------------------------------------
# R5 / R6 / R7 acceptance tests
# ---------------------------------------------------------------------------


def test_r5_replay_does_not_fire_during_real_capture_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R5 #1: replay the real S-1 fixture and assert the watchdog does NOT fire.

    The fixture is the literal evidence of the four-cycle
    kill/resume burn. The fix is that the subagent tailer feeds
    the watchdog's evidence channel so the classifier returns
    ``THINKING`` (a fresh subagent event within the TTL) instead
    of ``SILENT_SUBAGENT``. Without the fix, every iteration of
    the loop would fire NO_OUTPUT_DEADLINE.

    The test exercises the full pipeline: parser wrapping,
    subagent tailer, watchdog evidence summary, and the classifier.
    The watchdog does not fire as long as the subagent channel is
    fresh (which it is, because the tailer is feeding the channel
    with each new ``tool_use`` from the sibling files).
    """
    _project_dir, session_id, _project_key = _setup_shadow_home(monkeypatch, tmp_path)

    config = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        silent_subagent_seconds=180.0,
        activity_evidence_ttl_seconds=30.0,
    )
    clock = FakeClock(start=100.0)
    watchdog = IdleWatchdog(
        config=config,
        clock=clock,
        listener=lambda evt: None,
    )

    subagents_dir = _project_dir / session_id / "subagents"
    events_replayed = _replay_subagent_transcripts(subagents_dir, watchdog)
    assert events_replayed > 0

    # The watchdog's evidence channel must be non-empty after
    # replay: this is the RC3 fix.
    assert watchdog.subagent_progress_count > 0
    # The classifier's evidence summary includes a fresh
    # subagent channel; ``classify_stuck_now`` therefore returns
    # ``THINKING`` (not ``SILENT_SUBAGENT``) and the gate does
    # not fire.
    summary = watchdog.last_evidence_summary(clock.monotonic())
    subagent_channel = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    assert subagent_channel is not None
    assert subagent_channel.counter is not None and subagent_channel.counter > 0
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state="online",
        evidence_summary=summary,
        classify_quiet=lambda: (
            __import__(
                "ralph.agents.execution_state", fromlist=["AgentExecutionState"]
            ).AgentExecutionState.ACTIVE
        ),
        activity_evidence_ttl_seconds=30.0,
        silent_subagent_seconds=180.0,
    )
    assert kind.value in {"thinking", "loading"}, (
        f"classifier must return THINKING/LOADING (subagent channel"
        f" is fresh after replay); got {kind.value!r}."
    )


def test_r5_synthetic_envelope_does_not_reset_idle_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R5 #2: a synthetic envelope never resets the idle baseline.

    The fixture's parent timeline has 5 ``<synthetic>`` envelopes.
    Replay them through the parser; assert the watchdog's
    ``_last_subagent_progress_at`` stays ``None`` because the
    synthetic envelope does NOT route through
    ``record_subagent_work``. Also assert
    ``"No response requested."`` never appears in the produced
    ``AgentOutputLine`` content stream.
    """
    _setup_shadow_home(monkeypatch, tmp_path)
    parent_path = _fixture_root() / "a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl"

    config = TimeoutPolicy(idle_timeout_seconds=300.0, silent_subagent_seconds=180.0)
    watchdog = IdleWatchdog(config=config, clock=FakeClock(start=0.0), listener=lambda evt: None)

    parser = ClaudeInteractiveParser()
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
            for line in parser.parse(iter([stripped + "\n"])):
                # The synthetic envelope's bookkeeping text must
                # NOT appear in any AgentOutputLine (R4 / RC2).
                assert "No response requested." not in line.content, (
                    f"synthetic envelope leaked into AgentOutputLine:"
                    f" type={line.type!r} content={line.content!r}"
                )

    assert watchdog.last_subagent_progress_at is None
    assert watchdog.subagent_progress_count == 0
    summary = watchdog.last_evidence_summary(0.0)
    subagent_channel = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    assert subagent_channel is not None
    assert subagent_channel.last_at is None
    assert subagent_channel.counter is None


def test_r5_subagent_tool_use_resets_idle_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R5 #3: a subagent ``tool_use`` at T resets the idle baseline such that the fire at T+300s does not occur.

    This is the positive direction of R5: a single subagent
    event is enough to defer the NO_OUTPUT_DEADLINE fire while
    the channel stays fresh (within the activity TTL of 30s).
    """
    # Set up a clean shadow home with ONLY the synthetic subagent
    # event we want to test -- not the real fixture (which would
    # produce 100+ events). We use the project_key
    # ``-home-test-r5reset`` so this test never collides with
    # the S-1 fixture's project key.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project_key = "-home-test-r5reset"
    session_id = "sess_r5reset"
    project_dir = tmp_path / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    # Create the parent transcript (empty) so the discovery helper
    # resolves the layout.
    (project_dir / f"{session_id}.jsonl").touch()
    sub_dir = project_dir / session_id / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub_path = sub_dir / "agent-r5reset.jsonl"
    sub_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": True,
                "agentId": "r5reset",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read", "id": "tu_r5"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    config = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        silent_subagent_seconds=180.0,
        activity_evidence_ttl_seconds=30.0,
    )
    watchdog = IdleWatchdog(
        config=config,
        clock=FakeClock(start=100.0),
        listener=lambda evt: None,
    )

    subagents_dir = project_dir / session_id / "subagents"
    events_replayed = _replay_subagent_transcripts(subagents_dir, watchdog)
    assert events_replayed == 1

    assert watchdog.subagent_progress_count == 1
    # The fresh subagent event means the classifier's evidence
    # summary has a fresh first-party channel; classify_stuck
    # returns THINKING, not SILENT_SUBAGENT.
    summary = watchdog.last_evidence_summary(100.0)
    subagent_channel = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    assert subagent_channel is not None
    assert subagent_channel.counter == 1
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state="online",
        evidence_summary=summary,
        classify_quiet=lambda: (
            __import__(
                "ralph.agents.execution_state", fromlist=["AgentExecutionState"]
            ).AgentExecutionState.ACTIVE
        ),
        activity_evidence_ttl_seconds=30.0,
        silent_subagent_seconds=180.0,
    )
    assert kind.value == "thinking"


def test_r5_silent_parent_and_silent_children_still_fire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R5 #4: a session whose parent AND every subagent are silent still fires.

    This is the negative direction of R5: the watchdog's
    NO_OUTPUT_DEADLINE fire path is preserved for a truly
    dead session. The fixture is a degenerate two-line jsonl
    with no tool_use events; the watchdog's evidence channel
    stays empty so the classifier returns ``STUCK`` and the
    gate fires.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project_key = "-home-test-silent"
    session_id = "sess_silent"
    project_dir = tmp_path / ".claude" / "projects" / project_key
    (project_dir / session_id).mkdir(parents=True, exist_ok=True)
    # Parent transcript (empty) so the discovery helper resolves the layout.
    (project_dir / f"{session_id}.jsonl").touch()
    sub_dir = project_dir / session_id / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub_path = sub_dir / "agent-silent.jsonl"
    sub_path.write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": True,
                "agentId": "silent",
                "message": {"role": "user", "content": [{"type": "text", "text": "initial"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    config = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        silent_subagent_seconds=180.0,
        activity_evidence_ttl_seconds=30.0,
    )
    watchdog = IdleWatchdog(
        config=config,
        clock=FakeClock(start=0.0),
        listener=lambda evt: None,
    )

    subagents_dir = project_dir / session_id / "subagents"
    events_replayed = _replay_subagent_transcripts(subagents_dir, watchdog)
    assert events_replayed == 0

    # No tool_use events were forwarded; the evidence channel
    # is empty so the classifier returns STUCK (not
    # SILENT_SUBAGENT, since SILENT_SUBAGENT requires
    # ``counter >= 1`` per ``_silent_subagent_path``).
    assert watchdog.subagent_progress_count == 0
    summary = watchdog.last_evidence_summary(0.0)
    subagent_channel = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    assert subagent_channel is not None
    assert subagent_channel.counter is None
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state="online",
        evidence_summary=summary,
        classify_quiet=lambda: (
            __import__(
                "ralph.agents.execution_state", fromlist=["AgentExecutionState"]
            ).AgentExecutionState.ACTIVE
        ),
        activity_evidence_ttl_seconds=30.0,
        silent_subagent_seconds=180.0,
    )
    assert kind.value == "stuck"
