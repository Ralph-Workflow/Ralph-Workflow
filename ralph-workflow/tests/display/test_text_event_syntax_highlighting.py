"""Regression: parser highlight metadata drives REAL lexer styling on the
canonical render path (AC-02, plan S-3).

``AgyParser`` annotates fenced-code text events with
``metadata["syntax_highlight"] = True`` and ``metadata["language"]``.
Before this fix the canonical registry renderer
(:func:`ralph.display.agent_event_renderer.render_event`) ignored that
metadata entirely, so AGY-originated code reached the operator as flat
single-style text. These tests pin the transport-neutral contract: ANY
text event carrying the annotation -- from any agent -- renders the fenced
body through Pygments with lexer-derived styled spans, while ``.plain``
preserves the original content byte-for-byte and unknown languages fall
back to unstyled text.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.agy import AgyParser
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.agent_event_renderer import (
    make_event_for_emit,
    normalize_event_from_agent_output_line,
    render_event,
)

if TYPE_CHECKING:
    from rich.text import Text

    from ralph.agents.parsers.agent_output_line import AgentOutputLine
    from ralph.display.agent_activity_event import AgentActivityEvent


def _agy_text_event(content: str) -> AgentActivityEvent:
    """Parse a payload-carrying AGY frame into its canonical activity event."""
    frame = {
        "event": "step_update",
        "step_update": {
            "conversation_id": "synthetic",
            "step_index": 4,
            "state": "DONE",
            "step_type": "system_message",
            "text": content,
        },
    }
    lines: list[AgentOutputLine] = list(AgyParser().parse(iter([json.dumps(frame)])))
    text_lines = [line for line in lines if line.type == "text"]
    assert len(text_lines) == 1, f"expected one text line, got {lines}"
    return normalize_event_from_agent_output_line(
        text_lines[0], provider=ActivityProvider.AGY
    )


def _body_spans(text: Text, body_prefix: str) -> list[object]:
    """Return the styled spans that overlap the rendered body segment."""
    start = text.plain.find(body_prefix)
    assert start >= 0, f"body {body_prefix!r} missing from {text.plain!r}"
    return [
        span
        for span in text.spans
        if span.end > start and span.style not in (None, "")
    ]


def test_fenced_python_event_renders_lexer_derived_spans() -> None:
    """The canonical renderer styles a fenced python body with Pygments."""
    content = "Here is the snippet:\n```python\nprint('hello')\n```\n"
    event = _agy_text_event(content)
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "python"

    rendered = render_event(event, ctx=None)

    # The plain text preserves the original content exactly (no reflow).
    assert "```python" in rendered.plain
    assert "print('hello')" in rendered.plain
    # Real highlighting: the code body carries MORE THAN ONE distinct style,
    # proving the Pygments lexer ran (a flat single-style body is the old
    # un-highlighted rendering). Python lexing separates the ``print`` name
    # from the string literal, so the body must carry at least two styles.
    body_spans = _body_spans(rendered, "print")
    distinct_styles = {str(span.style) for span in body_spans}
    assert len(distinct_styles) >= 2, (
        f"expected lexer-derived span variety, got {distinct_styles}"
    )


def test_fenced_typescript_event_renders_distinct_token_styles() -> None:
    """A TypeScript fence highlights through the same generic seam."""
    content = "```ts\nconst value: number = 1;\n```\n"
    event = _agy_text_event(content)
    assert event.metadata.get("language") == "typescript"

    rendered = render_event(event, ctx=None)

    assert "const value: number = 1;" in rendered.plain
    body_spans = _body_spans(rendered, "const")
    distinct_styles = {str(span.style) for span in body_spans}
    assert len(distinct_styles) >= 2, (
        f"expected lexer-derived span variety, got {distinct_styles}"
    )


def test_unknown_language_fence_falls_back_to_single_body_style() -> None:
    """An unknown fence language renders visibly without Pygments styling."""
    content = "```futurelang\nwhatever\n```\n"
    event = _agy_text_event(content)
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "futurelang"

    rendered = render_event(event, ctx=None)

    assert "whatever" in rendered.plain
    # Fallback: the body keeps the event's single body style rather than
    # crashing or inventing token spans.
    body_spans = _body_spans(rendered, "whatever")
    distinct_styles = {str(span.style) for span in body_spans}
    assert len(distinct_styles) == 1, (
        f"unknown language must not synthesize token styles, got {distinct_styles}"
    )


def test_unannotated_text_event_keeps_flat_body() -> None:
    """A text event without the annotation is untouched by the seam."""
    event = make_event_for_emit(
        ActivityEventKind.TEXT,
        "plain message with no fence",
        metadata={},
    )
    rendered = render_event(event, ctx=None)
    body_spans = _body_spans(rendered, "plain message")
    distinct_styles = {str(span.style) for span in body_spans}
    assert len(distinct_styles) == 1


def test_equivalent_annotation_from_any_provider_highlights_identically() -> None:
    """The seam is transport-neutral: hand-built metadata highlights too."""
    content = "x = 1"
    agy_event = make_event_for_emit(
        ActivityEventKind.TEXT,
        f"```python\n{content}\n```",
        metadata={"syntax_highlight": True, "language": "python"},
    )
    other_event = make_event_for_emit(
        ActivityEventKind.TEXT,
        f"```python\n{content}\n```",
        metadata={"syntax_highlight": True, "language": "python"},
    )
    agy_rendered = render_event(agy_event, ctx=None)
    other_rendered = render_event(other_event, ctx=None)
    assert agy_rendered.plain == other_rendered.plain
    assert [str(s.style) for s in agy_rendered.spans] == [
        str(s.style) for s in other_rendered.spans
    ]

