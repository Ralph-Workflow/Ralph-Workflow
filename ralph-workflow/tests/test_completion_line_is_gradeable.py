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


def _completion_line(session: object, workspace: object, summary: str) -> str:
    result = handle_declare_complete(session, workspace, {"summary": summary})
    assert result.is_error is False, result
    text = result.content[0].text
    assert isinstance(text, str)
    return text.splitlines()[0]


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


@pytest.mark.parametrize("name", sorted(_OTHER_EMITTED_LINES))
def test_a_torn_write_is_graded_at_every_cut_but_a_measured_residue(name: str) -> None:
    """Sweep EVERY cut point, not the one a fixture happens to pick.

    A torn write severs the following line at an arbitrary byte, so
    pinning cut=0 measured almost nothing: an earlier grader excused 61
    of 83 cuts with the suite green, and a commit message of mine
    reported a residue of "0 of 83" that was measured on a different
    sweep and was simply wrong for this one.

    What survives is exactly one shape: a remainder that is ALL DIGITS
    welds onto this line's timestamp instead of breaking it. Bounding
    the field to a plausible epoch length (an epoch second needs ten
    digits, eleven until the year 5138) reduces that to the one or two
    digits that keep the total inside the bound. That cannot be closed
    without knowing the timestamp itself, so it is measured here rather
    than described.
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

    assert all(remainder.isdigit() for remainder in clean), clean
    assert len(clean) <= 2, clean
    # Not vacuous: an untorn genuine line is still recognised, and the
    # untruncated pair is still graded.
    assert is_whole_canonical_session_line(_GENUINE)
    assert not is_whole_canonical_session_line(_GENUINE + second)
