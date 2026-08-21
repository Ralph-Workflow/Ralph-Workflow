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
