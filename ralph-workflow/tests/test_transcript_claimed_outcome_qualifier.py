from __future__ import annotations

from ralph.agents.parsers.agy import AgyParser
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.agent_event_renderer import render_event
from ralph.display.context import make_display_context

_AGY_RESULT_SUCCESS = (
    '{"event":"result","result":{"status":"SUCCESS","duration_seconds":3.58,"num_turns":1}}'
)


def test_agy_result_frame_marks_stop_event_as_transcript_claimed() -> None:
    parser = AgyParser()
    events = list(parser.parse(iter([_AGY_RESULT_SUCCESS])))
    stop_events = [e for e in events if e.type == "stop"]
    assert len(stop_events) == 1
    assert stop_events[0].metadata.get("_transcript_claimed_outcome") is True
    assert "agy result SUCCESS" in stop_events[0].content


def test_lifecycle_renderer_qualifies_transcript_claimed_outcome() -> None:
    from ralph.display.activity_provider import ActivityProvider

    event = AgentActivityEvent(
        provider=ActivityProvider.AGY,
        kind=ActivityEventKind.LIFECYCLE,
        content="agy result SUCCESS (3.58s, 1 turn)",
        metadata={"_transcript_claimed_outcome": True},
    )
    ctx = make_display_context()
    rendered = render_event(event, ctx, unit_id="agy/test-model")
    rendered_str = rendered.plain
    assert "[transcript]" in rendered_str
    assert "agy result SUCCESS" in rendered_str


def test_lifecycle_renderer_does_not_qualify_non_transcript_lifecycle_events() -> None:
    from ralph.display.activity_provider import ActivityProvider

    event = AgentActivityEvent(
        provider=ActivityProvider.AGY,
        kind=ActivityEventKind.LIFECYCLE,
        content="phase: planning",
        metadata={},
    )
    ctx = make_display_context()
    rendered = render_event(event, ctx, unit_id="agy/test-model")
    assert "[transcript]" not in rendered.plain
