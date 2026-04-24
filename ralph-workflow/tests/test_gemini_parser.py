"""Tests for GeminiParser thought=True detection and thinking type emission."""

from __future__ import annotations

import json

from ralph.agents.parsers.gemini import GeminiParser


def test_candidates_part_thought_true_emits_thinking() -> None:
    """A candidates response part with thought=True yields type='thinking'."""
    parser = GeminiParser()
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"thought": True, "text": "Thinking about Y"}],
                    "role": "model",
                }
            }
        ]
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1, f"Expected 1 thinking result, got: {results}"
    assert thinking[0].content == "Thinking about Y"


def test_candidates_part_thought_false_emits_text() -> None:
    """A candidates response part with thought=False yields type='text'."""
    parser = GeminiParser()
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"thought": False, "text": "Normal response text"}],
                    "role": "model",
                }
            }
        ]
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    text_results = [r for r in results if r.type == "text"]
    thinking = [r for r in results if r.type == "thinking"]
    assert len(text_results) == 1, f"Expected 1 text result, got: {results}"
    assert thinking == [], f"Expected no thinking results, got: {thinking}"
    assert text_results[0].content == "Normal response text"


def test_candidates_part_no_thought_key_emits_text() -> None:
    """A candidates response part with no thought key yields type='text'."""
    parser = GeminiParser()
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Plain text from Gemini"}],
                    "role": "model",
                }
            }
        ]
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Plain text from Gemini"


def test_candidates_mixed_thought_and_text_parts() -> None:
    """Mixed thought=True and thought=False parts in one response."""
    parser = GeminiParser()
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "Reasoning here"},
                        {"thought": False, "text": "Final answer"},
                    ],
                    "role": "model",
                }
            }
        ]
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    text_results = [r for r in results if r.type == "text"]
    assert len(thinking) == 1
    assert thinking[0].content == "Reasoning here"
    assert len(text_results) == 1
    assert text_results[0].content == "Final answer"


def test_candidates_thought_none_emits_text() -> None:
    """A candidates part with thought=None yields type='text'."""
    parser = GeminiParser()
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"thought": None, "text": "Text with null thought"}],
                    "role": "model",
                }
            }
        ]
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"thought=None must not emit thinking, got: {thinking}"
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1


def test_message_part_thought_true_emits_thinking() -> None:
    """_parse_message with a part that has thought=True emits type='thinking'."""
    parser = GeminiParser()
    payload = {
        "type": "message",
        "parts": [{"thought": True, "text": "Internal reasoning"}],
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1
    assert thinking[0].content == "Internal reasoning"


def test_message_part_thought_false_emits_text() -> None:
    """_parse_message with a part that has thought=False emits type='text'."""
    parser = GeminiParser()
    payload = {
        "type": "message",
        "parts": [{"thought": False, "text": "Visible text"}],
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    text_results = [r for r in results if r.type == "text"]
    thinking = [r for r in results if r.type == "thinking"]
    assert len(text_results) == 1
    assert thinking == []


def test_candidates_empty_text_part_skipped() -> None:
    """Candidates parts with empty text are skipped."""
    parser = GeminiParser()
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"thought": True, "text": ""}, {"text": "Real content"}],
                    "role": "model",
                }
            }
        ]
    }
    line = f"data: {json.dumps(payload)}"
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    text_results = [r for r in results if r.type == "text"]
    # Empty thought part is skipped; text part emits normally
    assert thinking == []
    assert len(text_results) == 1
    assert text_results[0].content == "Real content"
