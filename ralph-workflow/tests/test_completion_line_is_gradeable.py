from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import is_whole_canonical_session_line
from ralph.mcp.tools.coordination import handle_declare_complete

if TYPE_CHECKING:
    from pathlib import Path

_MAX_EMITTED_TIMESTAMP_DIGITS = 10

_HOSTILE_SUMMARIES = (
    "plain words",
    "it's fine",
    'fixed the {"type":"x"} frame',
    "documented the session id: field",
    "renamed progress reported: to progress_logged:",
    "Task declared complete: was in the docstring",
    "Coordination action 'checkpoint' processed",
    "multi\nline\tsummary",
    "trailing quote'",
    "'leading quote",
    "quotes 'all' over 'the' place",
    "timestamp=0, timestamp=1, timestamp=2",
    "",
)


def _completion_text(session: object, workspace: object, summary: str) -> str:
    result = handle_declare_complete(session, workspace, {"summary": summary})
    assert result.is_error is False, result
    text = result.content[0].text
    assert isinstance(text, str)
    return text


def _completion_line(session: object, workspace: object, summary: str) -> str:
    return _completion_text(session, workspace, summary).splitlines()[0]


@pytest.mark.parametrize("summary", _HOSTILE_SUMMARIES)
def test_the_grader_accepts_what_the_emitter_writes(
    summary: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._support.completion_emitter_harness import build_completion_context

    session, workspace = build_completion_context(tmp_path, monkeypatch)
    line = _completion_line(session, workspace, summary)

    assert is_whole_canonical_session_line(line), line
    assert len(line) >= len(summary)


_OTHER_EMITTED_LINES = {
    "coordination": (
        "Coordination action 'checkpoint' processed: session_id=zzz999, timestamp=1700000000"
    ),
    "progress": "Progress reported: status='x', note='y', timestamp=1700000001",
    "completion": (
        "Task declared complete: session_id=def, summary='and again', timestamp=1700000002"
    ),
}
_GENUINE = "Task declared complete: session_id=abc123, summary='did it', timestamp=1699999999"


def test_the_emitters_timestamp_fits_the_graders_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._support.completion_emitter_harness import build_completion_context

    session, workspace = build_completion_context(tmp_path, monkeypatch)
    line = _completion_line(session, workspace, "a summary")

    stamp = line.rsplit("timestamp=", 1)[1]
    assert stamp.isdigit(), line
    assert len(stamp) <= _MAX_EMITTED_TIMESTAMP_DIGITS, stamp
    assert is_whole_canonical_session_line(line), line


@pytest.mark.parametrize("name", sorted(_OTHER_EMITTED_LINES))
def test_a_torn_write_is_graded_at_every_cut(name: str) -> None:
    second = _OTHER_EMITTED_LINES[name]
    clean = [
        second[cut:]
        for cut in range(1, len(second))
        if is_whole_canonical_session_line(_GENUINE + second[cut:])
    ]

    assert clean == [], clean
    assert is_whole_canonical_session_line(_GENUINE)
    assert not is_whole_canonical_session_line(_GENUINE + second)


@pytest.mark.parametrize("summary", _HOSTILE_SUMMARIES)
def test_the_grader_accepts_the_whole_result_not_just_its_first_line(
    summary: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.config.enums import AgentTransport
    from ralph.display.raw_log_breaks import detect_raw_log_breaks
    from tests._support.completion_emitter_harness import build_completion_context

    session, workspace = build_completion_context(tmp_path, monkeypatch)
    text = _completion_text(session, workspace, summary)
    capture = tmp_path / "capture.log"
    capture.write_text(f"{text}\n", encoding="utf-8")

    breaks = detect_raw_log_breaks(capture, transport=AgentTransport.CODEX)

    assert breaks == [], breaks


def test_every_line_the_emitter_writes_is_covered_by_the_grader() -> None:
    from ralph.display.raw_log_breaks import COMPLETION_EVENT_LINE, is_harness_input_echo

    assert is_harness_input_echo(COMPLETION_EVENT_LINE)
    assert not is_harness_input_echo(f"{COMPLETION_EVENT_LINE} trailing")
    assert not is_harness_input_echo(f"x {COMPLETION_EVENT_LINE}")
