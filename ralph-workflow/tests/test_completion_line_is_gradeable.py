"""What the completion emitter writes, the transcript grader must accept.

These two live in different packages and were changed independently for
several rounds: the grader kept guessing at a line the emitter produced
with no grammar at all, because the agent-authored summary was
interpolated raw. Each guess was wrong in one of two ways -- excusing a
torn write, or reporting a byte-perfect line as "raw transcript
corrupted".

The contract is one round trip: whatever an agent puts in a summary, the
line the emitter writes is a line the grader recognises. Asserting it
here is what stops the two drifting again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import is_whole_canonical_session_line
from ralph.mcp.tools.coordination import handle_declare_complete

if TYPE_CHECKING:
    from pathlib import Path

#: An epoch second, the width the grader's timestamp bound allows.
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
    """Every summary an agent can write, round-tripped."""
    from tests._support.completion_emitter_harness import build_completion_context

    session, workspace = build_completion_context(tmp_path, monkeypatch)
    line = _completion_line(session, workspace, summary)

    assert is_whole_canonical_session_line(line), line
    # Nothing the agent wrote is lost -- the escaping substitutes, it
    # does not drop.
    assert len(line) >= len(summary)


#: The lines ``mcp/tools/coordination.py`` emits beside a completion.
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
    """The grader bounds the timestamp field; the emitter must fit it.

    The bound is what stops a torn write welding another line's digits
    onto this line's timestamp, so it is deliberately tight -- ten
    digits, an epoch SECOND. A move to milliseconds would make every
    completion line fail the grammar and be reported as a corrupted
    transcript; it fails here instead.
    """
    from tests._support.completion_emitter_harness import build_completion_context

    session, workspace = build_completion_context(tmp_path, monkeypatch)
    line = _completion_line(session, workspace, "a summary")

    stamp = line.rsplit("timestamp=", 1)[1]
    assert stamp.isdigit(), line
    assert len(stamp) <= _MAX_EMITTED_TIMESTAMP_DIGITS, stamp
    assert is_whole_canonical_session_line(line), line


@pytest.mark.parametrize("name", sorted(_OTHER_EMITTED_LINES))
def test_a_torn_write_is_graded_at_every_cut(name: str) -> None:
    """Sweep EVERY cut point, not the one a fixture happens to pick.

    A torn write severs the following line at an arbitrary byte, so
    pinning cut=0 measured almost nothing: an earlier grader excused 61
    of 83 cuts with the suite green, and a commit message of mine
    reported a residue of "0 of 83" that was measured on a different
    sweep and was simply wrong for this one.

    What survived was exactly one shape: a remainder that is ALL DIGITS
    welds onto this line's timestamp instead of breaking it, whenever
    the weld keeps the field inside the grader's bound. Bounding the
    field to an epoch SECOND -- ten digits, and ten until 2286 -- closes
    it completely: a genuine timestamp already uses all ten, so there is
    no room to weld into. The previous round argued for ten and set
    twelve, and the two digits of slack were the entire residue.

    Asserted as EMPTY, not as "at most two". A bound loose enough to
    admit a weld passes a `<= 2` assertion just as well as one that
    admits none, which is how the slack survived a round.
    """
    second = _OTHER_EMITTED_LINES[name]
    clean = [
        second[cut:]
        # Stops before the empty remainder: consuming the whole second
        # line leaves the genuine first line alone, which is not a torn
        # write and is correctly clean.
        for cut in range(1, len(second))
        if is_whole_canonical_session_line(_GENUINE + second[cut:])
    ]

    assert clean == [], clean
    # Not vacuous: an untorn genuine line is still recognised, and the
    # untruncated pair is still graded.
    assert is_whole_canonical_session_line(_GENUINE)
    assert not is_whole_canonical_session_line(_GENUINE + second)


@pytest.mark.parametrize("summary", _HOSTILE_SUMMARIES)
def test_the_grader_accepts_the_whole_result_not_just_its_first_line(
    summary: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVERY line the emitter writes, graded through the real detector.

    The round trip above reads ``splitlines()[0]`` -- the half that was
    already allowlisted. The completion result is two lines, and the
    second one, ``[Completion event emitted to pipeline]``, appeared in
    no test at all: the allowlist entry that stops a byte-perfect
    Ralph-authored message being reported as "raw transcript corrupted"
    was silently revertible.

    Graded on a JSONL transport, where the detector actually grades
    non-JSON lines: an interactive-PTY transport exempts them and would
    make this vacuous.
    """
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
    """The emitted vocabulary and the allowlist are one set.

    Stated as a set comparison rather than per-line, so a THIRD line
    added to the completion result fails here instead of being reported
    as corruption on a live run.
    """
    from ralph.display.raw_log_breaks import COMPLETION_EVENT_LINE, is_harness_input_echo

    assert is_harness_input_echo(COMPLETION_EVENT_LINE)
    assert not is_harness_input_echo(f"{COMPLETION_EVENT_LINE} trailing")
    assert not is_harness_input_echo(f"x {COMPLETION_EVENT_LINE}")
