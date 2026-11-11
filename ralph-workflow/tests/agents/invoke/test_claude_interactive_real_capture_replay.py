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


def _watchdog_with_activity_recorder(
    *,
    silent_subagent_seconds: float = 180.0,
    idle_timeout_seconds: float = 300.0,
    activity_ttl_seconds: float = 30.0,
) -> tuple[IdleWatchdog, list[str]]:
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
                if obj.get("type") == "assistant":
                    msg = obj.get("message", {})
                    if isinstance(msg, dict) and msg.get("model") == "<synthetic>":
                        assert event.kind != "text", (
                            f"synthetic envelope leaked into text: {event.text!r}"
                        )
                        assert event.kind != "output", (
                            f"synthetic envelope leaked into output: {event.text!r}"
                        )

    assert lifecycle_events >= 5, (
        f"expected >=5 synthetic lifecycle events from the fixture, got {lifecycle_events}"
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
        tails._discover_new_files()
        tails._advance_all_tails()
    finally:
        stop.set()
        tails.stop()

    return len(subagent_calls)


def test_real_capture_replay_does_not_produce_text_for_synthetic_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    _project_dir, session_id, project_key = _setup_shadow_home(monkeypatch, tmp_path)
    watchdog, subagent_calls = _watchdog_with_activity_recorder()
    parent_path = _fixture_root() / "a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl"

    parent_tool_use = _drive_parent_timeline_through_parser(parent_path, watchdog, subagent_calls)
    assert parent_tool_use >= 1, (
        "the parent transcript must emit at least one Agent dispatch"
        " tool_use event; this is the RC1 evidence that the parent"
        " delegates work to in-process subagents."
    )
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

    subagent_events = _drive_subagent_tails(
        project_key=project_key,
        session_id=session_id,
        watchdog=watchdog,
        subagent_calls=subagent_calls,
    )

    assert subagent_events > 0, (
        "subagent tailer did not forward any events from the fixture;"
        " RC3 is broken: the watchdog's evidence channel stays empty"
        " even when subagents wrote to disk."
    )
    progress_count = getattr(watchdog, "_subagent_progress_count", 0)
    assert progress_count > 0, (
        f"_subagent_progress_count should be > 0 after replay, got {progress_count}"
    )


def test_real_capture_replay_does_not_fire_synthetic_envelope_through_idle_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_shadow_home(monkeypatch, tmp_path)
    watchdog, _subagent_calls = _watchdog_with_activity_recorder()
    parent_path = _fixture_root() / "a4731909-31bc-4ad5-bac9-cd59ee7e0615.jsonl"

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
    assert watchdog._last_subagent_progress_at is None


def test_real_capture_replay_subagents_dir_contains_issidechain_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_shadow_home(monkeypatch, tmp_path)
    found = find_claude_subagent_transcripts("a4731909-31bc-4ad5-bac9-cd59ee7e0615")
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
