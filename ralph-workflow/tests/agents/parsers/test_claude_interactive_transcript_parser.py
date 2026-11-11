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


def _top_level_api_error_record() -> str:
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


def test_top_level_api_error_emits_one_error_event_no_text_no_baseline_reset() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_top_level_api_error_record())
    error_events = [e for e in events if e.kind == "error"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(error_events) == 1, (
        f"expected exactly one error event, got {[e.kind for e in events]}"
    )
    assert text_events == []
    assert error_events[0].text


def test_nested_api_error_legacy_wire_emits_one_error_event_no_text() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_nested_api_error_record())
    error_events = [e for e in events if e.kind == "error"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(error_events) == 1
    assert text_events == []


def test_synthetic_envelope_emits_one_lifecycle_event_no_text() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_synthetic_record())
    lifecycle_events = [e for e in events if e.kind == "lifecycle"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(lifecycle_events) == 1, (
        f"expected exactly one lifecycle event, got {[e.kind for e in events]}"
    )
    assert text_events == []
    assert "No response requested." in lifecycle_events[0].text


def test_no_response_requested_literal_absent_from_agent_text_stream() -> None:
    for synthetic_text in (
        "No response requested.",
        "Resuming session.",
        "",
        "future bookkeeping payload",
        "very different text that should still be suppressed",
    ):
        parser = ClaudeInteractiveTranscriptParser()
        events = parser.feed(_synthetic_record(text=synthetic_text))
        for event in events:
            if event.kind in {"output", "text"}:
                pytest.fail(f"synthetic text leaked into {event.kind!r} stream: {event.text!r}")


def test_synthetic_zero_usage_heuristic_envelope_classified_as_synthetic() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_synthetic_record_zero_usage_heuristic())
    lifecycle_events = [e for e in events if e.kind == "lifecycle"]
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert len(lifecycle_events) == 1
    assert text_events == []


def test_normal_assistant_record_produces_text_event() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    events = parser.feed(_normal_assistant_record("real model output"))
    text_events = [e for e in events if e.kind in {"output", "text"}]
    assert text_events, f"normal envelope must produce a text event, got {[e.kind for e in events]}"
    assert any("real model output" in e.text for e in text_events)


def test_classifier_returns_decision_for_each_envelope_placement() -> None:
    top_level_api_error = classify_assistant_record(json.loads(_top_level_api_error_record()))
    nested_api_error = classify_assistant_record(json.loads(_nested_api_error_record()))
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

    assert top_level_api_error.error_text
    assert synthetic.synthetic_text
    assert normal.error_text == ""
    assert normal.synthetic_text == ""


def test_classifier_handles_non_dict_input_defensively() -> None:
    assert classify_assistant_record(None).envelope == ENVELOPE_NORMAL
    assert classify_assistant_record([]).envelope == ENVELOPE_NORMAL
    assert classify_assistant_record("not a dict").envelope == ENVELOPE_NORMAL
    assert classify_assistant_record({}).envelope == ENVELOPE_NORMAL


def test_classifier_priority_api_error_over_synthetic_when_both_match() -> None:
    both_record = json.loads(_synthetic_record())
    both_record["isApiErrorMessage"] = True
    decision = classify_assistant_record(both_record)
    assert decision.envelope == ENVELOPE_API_ERROR


def test_headless_parser_top_level_api_error_emits_error_line_no_text() -> None:
    parser = ClaudeParser()
    lines = list(parser.classify_line(_top_level_api_error_record()))
    error_lines = [ln for ln in lines if ln.type == "error"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(error_lines) == 1
    assert text_lines == []


def test_headless_parser_nested_api_error_legacy_wire_emits_error_line_no_text() -> None:
    parser = ClaudeParser()
    lines = list(parser.classify_line(_nested_api_error_record()))
    error_lines = [ln for ln in lines if ln.type == "error"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(error_lines) == 1
    assert text_lines == []


def test_headless_parser_synthetic_envelope_emits_lifecycle_line_no_text() -> None:
    parser = ClaudeParser()
    lines = list(parser.classify_line(_synthetic_record()))
    lifecycle_lines = [ln for ln in lines if ln.type == "lifecycle"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(lifecycle_lines) == 1
    assert text_lines == []
    assert "No response requested." in lifecycle_lines[0].content


def test_headless_parser_zero_usage_heuristic_classifies_as_synthetic() -> None:
    parser = ClaudeParser()
    lines = list(parser.classify_line(_synthetic_record_zero_usage_heuristic()))
    lifecycle_lines = [ln for ln in lines if ln.type == "lifecycle"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(lifecycle_lines) == 1
    assert text_lines == []


def test_headless_parser_normal_envelope_produces_text_line() -> None:
    parser = ClaudeParser()
    lines = list(parser.classify_line(_normal_assistant_record("real model output")))
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert text_lines, f"normal envelope must produce a text line, got {[ln.type for ln in lines]}"
    assert any("real model output" in ln.content for ln in text_lines)


def test_interactive_parser_post_processor_drops_lifecycle_envelope() -> None:
    parser = ClaudeInteractiveParser()
    lines = list(parser.parse(iter([_synthetic_record()])))
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    lifecycle_lines = [ln for ln in lines if ln.type == "lifecycle"]
    assert text_lines == []
    assert lifecycle_lines == []
    for ln in lines:
        assert "No response requested." not in ln.content


def test_interactive_parser_post_processor_emits_error_for_api_error_envelope() -> None:
    parser = ClaudeInteractiveParser()
    lines = list(parser.parse(iter([_top_level_api_error_record()])))
    error_lines = [ln for ln in lines if ln.type == "error"]
    text_lines = [ln for ln in lines if ln.type in {"text", "output"}]
    assert len(error_lines) == 1
    assert text_lines == []


def test_classifier_decision_dataclass_is_frozen() -> None:
    decision = classify_assistant_record(json.loads(_normal_assistant_record()))
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        decision.envelope = "tampered"
