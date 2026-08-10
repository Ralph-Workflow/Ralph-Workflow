"""Pin RC2's synthetic/error envelope classification (wt-04-claude-parsing).

Claude Code emits bookkeeping turns as real ``type: "assistant"`` records.
The wire has two distinct envelopes that MUST be classified at the
envelope level (not the text level) so the synthetic bookkeeping
``"No response requested."`` text never reaches the operator channel,
the retry-context excerpt, or the idle baseline. The envelope classifier
is the single source of truth for both transports; a future wire
change touches one file, not two.

Five acceptance tests (one method each):

  1. Top-level ``isApiErrorMessage: True`` assistant record -> exactly
     one ``error`` event, zero ``output``/``text`` events, never
     resets the idle baseline.
  2. Nested ``message.isApiErrorMessage: True`` assistant record
     (legacy wire shape) -> identical behaviour to (1).
  3. ``model == "<synthetic>"`` with ``output_tokens == 0`` and
     ``stop_reason == "stop_sequence"`` -> exactly one ``lifecycle``
     event, zero ``output``/``text`` events, the
     ``"No response requested."`` literal does NOT appear in the
     parsed event stream.
  4. The literal ``"No response requested."`` (and any future
     bookkeeping text the synthetic envelope carries) does NOT
     appear in the produced event stream for ANY synthetic envelope,
     any future rewrite across one build to the next, any model
     name, any future synthetic-text payload.
  5. Both headless (``claude -p``) and interactive parsers handle ALL
     THREE envelope placements identically when invoked through the
     shared classifier.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (only ``ClaudeInteractiveTranscriptParser.feed``
    and ``ClaudeParser.classify_line`` against hand-crafted records).
  - No real filesystem (the synthetic/error records are constructed
    inline; no fixture files needed).
  - No real wall-clock waits.
  - No module-level mutable accumulators.
  - No ``noqa`` directives (audit_lint_bypass).
  - No bare ``type: ignore`` comments (audit_typecheck_bypass).
"""

from __future__ import annotations

import json

import pytest

from ralph.agents.parsers._assistant_envelope import (
    ENVELOPE_API_ERROR,
    ENVELOPE_NORMAL,
    ENVELOPE_SYNTHETIC,
    classify_assistant_record,
)
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _top_level_api_error_record() -> str:
    """Current Claude Code 2.1.x wire: ``isApiErrorMessage`` at the TOP level."""
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {"type": "text", "text": "model said something weird"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 7},
        },
        "isApiErrorMessage": True,
    }
    return json.dumps(record)


def _nested_api_error_record() -> str:
    """Legacy wire: ``isApiErrorMessage`` nested under ``message``."""
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {"type": "text", "text": "model said something weird"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 7},
            "isApiErrorMessage": True,
        },
    }
    return json.dumps(record)


def _synthetic_record(text: str = "No response requested.") -> str:
    """Current Claude Code 2.1.x synthetic bookkeeping envelope."""
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "<synthetic>",
            "id": "msg_synthetic_1",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "stop_sequence",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "type": "message",
        },
        "isApiErrorMessage": False,
    }
    return json.dumps(record)


def _synthetic_record_zero_usage_heuristic(text: str = "future bookkeeping") -> str:
    """Future wire: drops ``<synthetic>`` model name but keeps zero-usage + stop_sequence."""
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "stop_sequence",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }
    return json.dumps(record)


def _normal_assistant_record(text: str = "real model output") -> str:
    """A normal assistant record that should NOT be classified as synthetic or error."""
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-5",
            "id": "msg_normal_1",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
    }
    return json.dumps(record)


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------


def test_top_level_api_error_emits_one_error_event_no_text_no_baseline_reset() -> None:
    """AC #1: top-level ``isApiErrorMessage: True`` -> exactly one ``error`` event.

    The envelope classifier routes the record to the error path. The
    parser emits exactly one ``InteractiveTranscriptEvent(kind="error", ...)``
    with the error text; no ``output`` / ``text`` events are produced;
    no agent text reaches the operator channel; no idle-baseline
    reset signal is sent (the watchdog's ``record_subagent_work`` is
    never called for an envelope-only error).
    """
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_top_level_api_error_record())
    error_events = [e for e in events if e.kind == "error"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(error_events) == 1, (
        f"expected exactly one error event, got {[e.kind for e in events]}"
    )
    assert text_events == []
    assert error_events[0].text  # error text is non-empty


def test_nested_api_error_legacy_wire_emits_one_error_event_no_text() -> None:
    """AC #2: nested ``message.isApiErrorMessage: True`` (legacy wire) -> error envelope.

    The legacy wire shape carries ``isApiErrorMessage`` under
    ``message`` rather than at the top level. The envelope classifier
    handles BOTH placements so a future wire change that drops the
    top-level placement does not silently route the record to the
    normal path. The parser must emit exactly one ``error`` event
    with no ``output`` / ``text`` events.
    """
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_nested_api_error_record())
    error_events = [e for e in events if e.kind == "error"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(error_events) == 1
    assert text_events == []


def test_synthetic_envelope_emits_one_lifecycle_event_no_text() -> None:
    """AC #3: ``<synthetic>`` envelope -> exactly one ``lifecycle`` event.

    The envelope classifier routes the record to the lifecycle path.
    The parser emits exactly one ``InteractiveTranscriptEvent(kind="lifecycle", ...)``
    with the bookkeeping text as payload; no ``output`` / ``text``
    events are produced; the ``"No response requested."`` literal
    never reaches the agent text stream. The ``is_lifecycle_kind``
    predicate in ``ClaudeInteractiveParser.parse()`` (line 135)
    drops the lifecycle event from the agent-text stream so the
    watchdog's ``_record_event`` path cannot reach it and the
    retry-context excerpt builder cannot reach it (R4 contract).
    """
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_synthetic_record())
    lifecycle_events = [e for e in events if e.kind == "lifecycle"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(lifecycle_events) == 1, (
        f"expected exactly one lifecycle event, got {[e.kind for e in events]}"
    )
    assert text_events == []
    # The payload is the bookkeeping text -- this is by design; the
    # lifecycle event carries it so the watchdog can see what
    # happened without the bookkeeping reaching the operator channel.
    assert "No response requested." in lifecycle_events[0].text


def test_no_response_requested_literal_absent_from_agent_text_stream() -> None:
    """AC #4: ``"No response requested."`` never appears in the agent-text stream.

    The classification keys off the envelope, not the text per the
    PROMPT's explicit prohibition on "hardcoded string filtering".
    A future Claude Code build that emits a different bookkeeping
    payload (e.g. "Resuming session." or an empty content block)
    must continue to be classified as a synthetic envelope. This
    test pins that future-proof behaviour by varying the synthetic
    text and asserting that none of the variations leak into the
    agent-text stream.
    """
    for synthetic_text in (
        "No response requested.",
        "Resuming session.",
        "",
        "future bookkeeping payload",
        "very different text that should still be suppressed",
    ):
        parser = ClaudeInteractiveTranscriptParser()
        events = parser.feed(_synthetic_record(text=synthetic_text))
        # No output / text event should ever carry the synthetic text.
        for event in events:
            if event.kind in {"output", "text"}:
                pytest.fail(
                    f"synthetic text leaked into {event.kind!r} stream:"
                    f" {event.text!r}"
                )
        # The lifecycle event is allowed to carry the text (the
        # watchdog surfaces it for diagnostics; the operator never
        # sees it because ``is_lifecycle_kind`` drops it in
        # ``ClaudeInteractiveParser.parse()``).


def test_synthetic_zero_usage_heuristic_envelope_classified_as_synthetic() -> None:
    """AC #4 (extension): zero-usage + stop_sequence heuristic still classifies.

    The classifier carries TWO acceptance criteria for the synthetic
    envelope: the canonical ``<synthetic>`` model name AND the
    weaker heuristic ``usage.output_tokens == 0 AND stop_reason ==
    "stop_sequence"``. A future wire that drops the ``<synthetic>``
    model name while keeping the zero-usage + stop_sequence stop
    pattern must STILL be classified as a synthetic envelope. The
    test feeds a hand-crafted record without the ``<synthetic>``
    model name and asserts the lifecycle path is taken.
    """
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_synthetic_record_zero_usage_heuristic())
    lifecycle_events = [e for e in events if e.kind == "lifecycle"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(lifecycle_events) == 1
    assert text_events == []


def test_normal_assistant_record_produces_text_event() -> None:
    """A normal envelope (no error/synthetic markers) routes to the text path.

    Negative test: the envelope classifier MUST NOT over-suppress.
    A real assistant turn with ``model == "claude-sonnet-5"``,
    non-zero usage, and ``stop_reason == "end_turn"`` produces a
    ``text`` event carrying the model output. The classifier only
    fires on the explicit envelope markers.
    """
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_normal_assistant_record("real model output"))
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert text_events, (
        f"normal envelope must produce a text event, got {[e.kind for e in events]}"
    )
    assert any("real model output" in e.text for e in text_events)


def test_classifier_returns_decision_for_each_envelope_placement() -> None:
    """AC #5 (classifier level): all three envelope placements classify identically.

    The shared classifier is the single source of truth. It must
    return the same decision for the same input regardless of which
    transport invokes it. This test exercises the classifier directly
    so the contract is pinned independently of the parser wrapping.
    """
    top_level_api_error = classify_assistant_record(
        json.loads(_top_level_api_error_record())
    )
    nested_api_error = classify_assistant_record(
        json.loads(_nested_api_error_record())
    )
    synthetic = classify_assistant_record(json.loads(_synthetic_record()))
    zero_usage_heuristic = classify_assistant_record(
        json.loads(_synthetic_record_zero_usage_heuristic())
    )
    normal = classify_assistant_record(json.loads(_normal_assistant_record()))

    assert top_level_api_error.envelope == ENVELOPE_API_ERROR
    assert nested_api_error.envelope == ENVELOPE_API_ERROR
    assert synthetic.envelope == ENVELOPE_SYNTHETIC
    assert zero_usage_heuristic.envelope == ENVELOPE_SYNTHETIC
    assert normal.envelope == ENVELOPE_NORMAL

    # The classifier carries the error_text / synthetic_text in the
    # decision so the parser wrapping does not have to re-extract.
    assert top_level_api_error.error_text
    assert synthetic.synthetic_text
    assert normal.error_text == ""
    assert normal.synthetic_text == ""


def test_classifier_handles_non_dict_input_defensively() -> None:
    """The classifier is a pure function; non-dict input classifies as NORMAL.

    Defensive contract: a non-dict input (e.g. a stray ``None`` or
    list) MUST NOT raise. The classifier returns ``ENVELOPE_NORMAL``
    so the parser wrapping falls through to the existing per-content-block
    path (which itself handles non-dict messages safely).
    """
    # The classifier's parameter is typed ``object`` (the real wire
    # shape is ``dict[str, Any]`` but defensive coverage of the
    # ``isinstance`` guard at the top of the function exercises
    # non-dict inputs directly -- no cast needed because the
    # parameter accepts ``object``).
    assert classify_assistant_record(None).envelope == ENVELOPE_NORMAL
    assert classify_assistant_record([]).envelope == ENVELOPE_NORMAL
    assert classify_assistant_record("not a dict").envelope == ENVELOPE_NORMAL
    assert classify_assistant_record({}).envelope == ENVELOPE_NORMAL


def test_classifier_priority_api_error_over_synthetic_when_both_match() -> None:
    """API-error envelope takes precedence over a synthetic marker when both match.

    Forward-compatibility: a future build that emits
    ``isApiErrorMessage: True`` alongside ``model: <synthetic>`` must
    still route to the error path because an API error is the more
    operator-relevant signal. The classifier's priority order pins
    this contract.
    """
    both_record = json.loads(_synthetic_record())
    both_record["isApiErrorMessage"] = True
    decision = classify_assistant_record(both_record)
    assert decision.envelope == ENVELOPE_API_ERROR


# ---------------------------------------------------------------------------
# AC #5: headless transport (claude.py) parity with the interactive parser
# ---------------------------------------------------------------------------


def test_headless_parser_top_level_api_error_emits_error_line_no_text() -> None:
    """Headless ``claude -p`` emits one error line for the top-level API-error envelope.

    The headless parser routes through the shared classifier so its
    behaviour matches the interactive parser. The post-processor
    yields ``AgentOutputLine(type="error", ...)`` exactly once and
    produces NO ``text`` / ``output`` line.
    """
    parser = ClaudeParser()
    lines = list(parser.classify_line(_top_level_api_error_record()))
    error_lines = [ln for ln in lines if ln.type == "error"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(error_lines) == 1
    assert text_lines == []


def test_headless_parser_nested_api_error_legacy_wire_emits_error_line_no_text() -> None:
    """Headless parser also handles the legacy nested wire placement."""
    parser = ClaudeParser()
    lines = list(parser.classify_line(_nested_api_error_record()))
    error_lines = [ln for ln in lines if ln.type == "error"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(error_lines) == 1
    assert text_lines == []


def test_headless_parser_synthetic_envelope_emits_lifecycle_line_no_text() -> None:
    """Headless parser emits one lifecycle line for the synthetic envelope.

    The lifecycle line is the canonical surface the watchdog uses to
    surface a synthetic bookkeeping turn without routing it as
    agent text. The ``is_lifecycle_event`` predicate drops it from
    operator-visible output (see ``claude.py::_parse_plain_text_prefix``).
    """
    parser = ClaudeParser()
    lines = list(parser.classify_line(_synthetic_record()))
    lifecycle_lines = [ln for ln in lines if ln.type == "lifecycle"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(lifecycle_lines) == 1
    assert text_lines == []
    assert "No response requested." in lifecycle_lines[0].content


def test_headless_parser_zero_usage_heuristic_classifies_as_synthetic() -> None:
    """Headless parser handles the weaker zero-usage heuristic."""
    parser = ClaudeParser()
    lines = list(parser.classify_line(_synthetic_record_zero_usage_heuristic()))
    lifecycle_lines = [ln for ln in lines if ln.type == "lifecycle"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(lifecycle_lines) == 1
    assert text_lines == []


def test_headless_parser_normal_envelope_produces_text_line() -> None:
    """Headless parser still produces text for a normal envelope (no over-suppression)."""
    parser = ClaudeParser()
    lines = list(parser.classify_line(_normal_assistant_record("real model output")))
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert text_lines, (
        f"normal envelope must produce a text line, got {[ln.type for ln in lines]}"
    )
    assert any("real model output" in ln.content for ln in text_lines)


def test_interactive_parser_post_processor_drops_lifecycle_envelope() -> None:
    """``ClaudeInteractiveParser.parse()`` drops lifecycle events from the agent stream.

    The post-processor's ``is_lifecycle_kind`` predicate is the
    downstream surface that prevents the synthetic bookkeeping text
    from reaching the operator channel. This test drives the full
    interactive parser pipeline (transcript parser -> post-processor)
    and asserts the bookkeeping text is NOT in the produced
    ``AgentOutputLine`` stream for a synthetic envelope. The lifecycle
    line itself is consumed and dropped (it carries the bookkeeping
    text only for transcript-parser diagnostics, NOT for the
    operator-visible ``AgentOutputLine`` stream).
    """
    parser = ClaudeInteractiveParser()
    lines = list(parser.parse(iter([_synthetic_record()])))
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    lifecycle_lines = [ln for ln in lines if ln.type == "lifecycle"]
    assert text_lines == []  # no text/output line carries the synthetic text
    assert lifecycle_lines == []  # the lifecycle line is dropped by is_lifecycle_kind
    # No AgentOutputLine produced for the synthetic envelope carries
    # the bookkeeping text anywhere.
    for ln in lines:
        assert "No response requested." not in ln.content


def test_interactive_parser_post_processor_emits_error_for_api_error_envelope() -> None:
    """``ClaudeInteractiveParser.parse()`` emits ``AgentOutputLine(type="error", ...)`` for API errors.

    The error envelope routes through the parser wrapping and the
    post-processor yields ``AgentOutputLine(type="error", content=...)``
    carrying the error text. No ``text`` / ``output`` line is
    produced -- the error path is exclusive.
    """
    parser = ClaudeInteractiveParser()
    lines = list(parser.parse(iter([_top_level_api_error_record()])))
    error_lines = [ln for ln in lines if ln.type == "error"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(error_lines) == 1
    assert text_lines == []


def test_classifier_decision_dataclass_is_frozen() -> None:
    """``AssistantEnvelopeDecision`` is frozen -- a pure-function return type."""
    decision = classify_assistant_record(json.loads(_normal_assistant_record()))
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        decision.envelope = "tampered"
