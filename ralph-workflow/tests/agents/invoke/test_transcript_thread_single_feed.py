"""wt-04-claude-parsing regression: the parent transcript line is fed exactly once.

``ClaudeInteractiveTranscriptParser`` is stateful: it tracks ``self.session_id``
and a ``_last_emitted_signature`` dedup cache across calls to ``feed()``.
``_pty_line_reader.PtyLineReader._transcript_thread`` previously called
``feed()`` on the SAME raw line twice per iteration -- once directly (to
route each parsed event to the subagent tailer), and again inside
``transcript_lines_from_event(line, parser=transcript_parser)`` (to build
the lines pushed to the operator-facing ``_lines_queue``). The second call
saw a parser that had already advanced past that line's session id / dedup
signature, so it silently returned FEWER OR ZERO events -- starving the
entire operator-facing output stream (no "Session ID: ..." line, no tool
activity, nothing) even while the parent transcript file on disk grew with
real content. This is exactly the live symptom observed in
``smoke-interactive-claude --subagents``: "session ID was not observed",
"no tool activity was observed" despite a real, growing transcript file.
"""

from __future__ import annotations

import json

from ralph.agents.invoke._pty_transcript import (
    transcript_lines_from_event,
    transcript_lines_from_events,
)
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)


def test_feeding_the_same_line_twice_through_one_parser_loses_the_session_event() -> None:
    """Locks in WHY double-feeding is wrong: pins the old buggy shape's output.

    This is not the fix -- it is the regression itself, preserved as a
    named pin so a future refactor cannot reintroduce the double-feed
    pattern without a visible, named test failure.
    """
    raw_line = json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-double-feed"})
    parser = ClaudeInteractiveTranscriptParser()

    parser.feed(raw_line)  # e.g. the subagent-tailer routing loop's own feed
    second_call_lines = transcript_lines_from_event(raw_line, parser=parser)

    assert second_call_lines == [], (
        "a second feed() on the same line through the same stateful parser "
        "returns nothing -- this is the bug, pinned so it stays understood"
    )


def test_transcript_lines_from_events_reuses_the_single_feed_call() -> None:
    """The fix: feed once, reuse the events for both routing and line-building."""
    raw_line = json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-single-feed"})
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed(raw_line)  # the ONE feed call _transcript_thread now makes
    # events is reused for subagent-tailer routing (not exercised here) AND:
    lines = transcript_lines_from_events(raw_line, events)

    assert lines == ["Session ID: sess-single-feed\n"]


def test_transcript_lines_from_events_matches_single_call_transcript_lines_from_event() -> None:
    """Same input, fed once each way, must agree -- events is a legitimate substitute for feed()."""
    raw_line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "ok"}],
                    }
                ]
            },
        }
    )

    parser_a = ClaudeInteractiveTranscriptParser()
    via_wrapper = transcript_lines_from_event(raw_line, parser=parser_a)

    parser_b = ClaudeInteractiveTranscriptParser()
    events_b = parser_b.feed(raw_line)
    via_events = transcript_lines_from_events(raw_line, events_b)

    assert via_wrapper == via_events == [f"{raw_line}\n"]


def test_transcript_thread_shape_survives_a_multi_line_replay() -> None:
    """Replays several distinct lines the way ``_transcript_thread`` does: feed once per line.

    A realistic mini-session: a ``mode`` record (session id), a
    ``tool_use`` assistant record, then a ``tool_result`` user record.
    Each line is fed through the SAME parser instance exactly once (the
    corrected ``_transcript_thread`` shape), and every line's activity
    must survive into the emitted lines -- none may be silently dropped
    by a phantom second feed.
    """
    lines_in = [
        json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-replay"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_agent_1",
                            "name": "Agent",
                            "input": {"description": "do work"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_agent_1",
                            "content": [{"type": "text", "text": "done"}],
                        }
                    ]
                },
            }
        ),
    ]
    parser = ClaudeInteractiveTranscriptParser()
    emitted: list[str] = []
    for raw_line in lines_in:
        events = parser.feed(raw_line)  # exactly once per line, as _transcript_thread does
        emitted.extend(transcript_lines_from_events(raw_line, events))

    assert emitted == [
        "Session ID: sess-replay\n",
        f"{lines_in[1]}\n",
        f"{lines_in[2]}\n",
    ]
