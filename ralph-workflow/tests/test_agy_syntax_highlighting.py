"""Regression tests for the AgyParser syntax-highlighting pass (S-8).

The AGY transport contract requires that any text payload derived from a
``system_message`` (or an equivalent content frame) carrying markdown code
fences is run through the project's syntax highlighter before it reaches
the rendering pipeline: the emitted ``text`` event carries
``metadata["syntax_highlight"] is True`` plus the detected language tag
under ``metadata["language"]``.

The measured v1.1.10-v1.1.13 wire frames are bodiless ``system_message``
step updates (see ``tests/display/_fixtures/agy_wire_provenance.md``), so
the payload-carrying frames replayed here are synthetic, labeled as such:
they pin the forward-compatible behaviour for the payload the step_type
exists to carry, while the bodiless contract stays pinned in
``tests/test_agy_parser.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from ralph.agents.parsers.agy import AgyParser

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine


def _parse(frames: list[dict[str, Any]] | list[str]) -> list[AgentOutputLine]:
    """Parse raw NDJSON lines or plain-text lines through a fresh parser."""
    lines = [
        frame if isinstance(frame, str) else json.dumps(frame) for frame in frames
    ]
    return list(AgyParser().parse(iter(lines)))


def _text_events(events: Iterator[AgentOutputLine] | list[AgentOutputLine]) -> list[AgentOutputLine]:
    return [event for event in events if event.type == "text"]


def _step(step_update: dict[str, Any]) -> dict[str, Any]:
    return {"event": "step_update", "step_update": step_update}


def test_system_message_step_with_fenced_python_yields_highlighted_text() -> None:
    """A payload-carrying ``system_message`` step emits highlighted text (S-8)."""
    frame = _step(
        {
            "conversation_id": "synthetic",
            "step_index": 4,
            "state": "DONE",
            "step_type": "system_message",
            "text": "Here is the snippet:\n```python\nprint('hello')\n```\n",
        }
    )
    events = _parse([frame])
    text_events = _text_events(events)
    assert len(text_events) == 1, f"expected one text event, got {text_events}"
    event = text_events[0]
    assert event.metadata is not None
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "python"


def test_system_message_step_with_fenced_shell_yields_highlighted_text() -> None:
    """Plan S-6: a shell fence emits ``syntax_highlight: True`` with the
    canonical Pygments alias, completing the python / ts / shell pin set."""
    frame = _step(
        {
            "conversation_id": "synthetic",
            "step_index": 5,
            "state": "DONE",
            "step_type": "system_message",
            "text": "Run it like this:\n```bash\ngit status --short\n```\n",
        }
    )
    events = _parse([frame])
    text_events = _text_events(events)
    assert len(text_events) == 1, f"expected one text event, got {text_events}"
    event = text_events[0]
    assert event.metadata is not None
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "bash"


def test_bodiless_system_message_step_stays_lifecycle() -> None:
    """The measured bodiless ``system_message`` contract is unchanged (S-8)."""
    frame = _step(
        {
            "conversation_id": "synthetic",
            "step_index": 4,
            "state": "DONE",
            "step_type": "system_message",
        }
    )
    events = _parse([frame])
    assert [event.type for event in events] == ["lifecycle"]
    assert events[0].content == "agy step system_message"
    assert not (events[0].metadata or {}).get("syntax_highlight")


def test_top_level_system_message_event_frame_is_highlighted_text() -> None:
    """A top-level ``system_message`` event projects to highlighted text (S-8)."""
    frame = {
        "event": "system_message",
        "message": "```ts\nconst value: number = 1;\n```\n",
    }
    events = _parse([frame])
    text_events = _text_events(events)
    assert len(text_events) == 1
    event = text_events[0]
    assert event.metadata is not None
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "typescript"


def test_agent_response_text_delta_fence_annotated_on_flush() -> None:
    """Equivalent content frames: a fenced ``text_delta`` flush is annotated."""
    frames = [
        _step(
            {
                "conversation_id": "synthetic",
                "step_index": 2,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "The fix:\n```rust\nfn main() {}\n```\n",
            }
        ),
        # The closing result frame is the structural boundary that flushes
        # the accumulated text.
        {
            "event": "result",
            "result": {
                "conversation_id": "synthetic",
                "status": "SUCCESS",
                "response": "",
                "duration_seconds": 0.5,
                "num_turns": 1,
            },
        },
    ]
    events = _parse(frames)
    text_events = _text_events(events)
    assert len(text_events) == 1
    event = text_events[0]
    assert "```rust" in event.content
    assert event.metadata is not None
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "rust"


def test_plain_text_fallback_fence_annotated_on_flush() -> None:
    """The plain-text (non-JSON) fallback path annotates fenced code too."""
    lines = [
        "Consider this implementation:",
        "```js",
        "const items = [];",
        "```",
    ]
    events = _parse(lines)
    text_events = _text_events(events)
    assert text_events, "expected the fallback lines to flush as a text event"
    event = text_events[0]
    assert "const items" in event.content
    assert event.metadata is not None
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "javascript"


def test_payload_without_fence_is_not_annotated() -> None:
    """Text without a fenced code block carries no highlight annotation."""
    frame = _step(
        {
            "conversation_id": "synthetic",
            "step_index": 4,
            "state": "DONE",
            "step_type": "system_message",
            "text": "Plain system notice with no code.",
        }
    )
    events = _parse([frame])
    text_events = _text_events(events)
    assert len(text_events) == 1
    assert "syntax_highlight" not in (text_events[0].metadata or {})


def test_unknown_fence_info_string_keeps_declared_language() -> None:
    """A fence language Pygments does not know keeps the declared tag."""
    frame = _step(
        {
            "conversation_id": "synthetic",
            "step_index": 4,
            "state": "DONE",
            "step_type": "system_message",
            "text": "```futurelang\nwhatever\n```\n",
        }
    )
    events = _parse([frame])
    text_events = _text_events(events)
    assert len(text_events) == 1
    assert text_events[0].metadata is not None
    assert text_events[0].metadata.get("syntax_highlight") is True
    assert text_events[0].metadata.get("language") == "futurelang"
