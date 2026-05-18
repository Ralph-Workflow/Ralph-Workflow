"""Unit tests for agent NDJSON parsers."""

from __future__ import annotations

import json
import json as _json
from typing import TYPE_CHECKING

from ralph.agents.parsers import (
    ClaudeParser,
    GenericParser,
    OpenCodeParser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_TWO_LINES = 2


def _make_lines(data: list[str]) -> Iterator[str]:
    """Helper to create line iterator from list."""
    return iter(data)



def test_generic_message_stop_type_is_suppressed() -> None:
    """Generic message_stop event type must produce no output."""
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"message_stop"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_content_block_start_type_is_suppressed() -> None:
    """Generic content_block_start event type must produce no output."""
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"content_block_start"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_content_block_stop_type_is_suppressed() -> None:
    """Generic content_block_stop event type must produce no output."""
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"content_block_stop"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_user_type_is_suppressed() -> None:
    """Generic user event type must produce no output."""
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"user"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_assistant_type_is_suppressed() -> None:
    """Generic assistant event type must produce no output."""
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"assistant"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_thinking_type_is_suppressed() -> None:
    """Generic thinking event type must produce no output."""
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"thinking"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_message_delta_type_is_suppressed() -> None:
    """Generic message_delta event type must produce no output."""
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"message_delta"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_assistant_is_suppressed() -> None:
    """claude/sonnet: assistant must be suppressed as a lifecycle marker."""
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: assistant"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_message_start_is_suppressed() -> None:
    """claude/sonnet: message_start must be suppressed as a lifecycle marker."""
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: message_start"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_message_stop_is_suppressed() -> None:
    """claude/sonnet: message_stop must be suppressed as a lifecycle marker."""
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: message_stop"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_content_block_start_is_suppressed() -> None:
    """claude/sonnet: content_block_start must be suppressed as a lifecycle marker."""
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: content_block_start"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_content_block_stop_is_suppressed() -> None:
    """claude/sonnet: content_block_stop must be suppressed as a lifecycle marker."""
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: content_block_stop"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_parser_tool_result_with_image_block_emits_placeholder() -> None:
    """Claude parser emits bounded placeholder for image block in tool_result, not an error."""

    parser = ClaudeParser()
    # tool_result blocks appear in assistant message content
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": [
                            {
                                "type": "image",
                                "source": {"media_type": "image/png", "data": "abc123"},
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    # Must yield a tool_result, not an error
    assert any(r.type == "tool_result" for r in results), (
        f"Expected a tool_result line, got: {[r.type for r in results]}"
    )
    assert not any(r.type == "error" for r in results), (
        f"Got unexpected error line(s): {[r for r in results if r.type == 'error']}"
    )
    # The content must include a placeholder summary for the image
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[image:" in r.content for r in tool_results), (
        f"Expected '[image:' in tool_result content, got: {[r.content for r in tool_results]}"
    )


def test_claude_parser_tool_result_with_resource_reference_emits_placeholder() -> None:
    """Claude parser emits bounded placeholder for resource_reference block in tool_result."""

    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_456",
                        "content": [
                            {
                                "type": "resource_reference",
                                "uri": "ralph://media/abc123",
                                "modality": "pdf",
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results)
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[pdf: ralph://media/abc123]" in r.content for r in tool_results), (
        f"Expected '[pdf: ...]' in content, got: {[r.content for r in tool_results]}"
    )


def test_claude_parser_tool_result_with_mixed_text_and_image_preserves_text() -> None:
    """Claude parser preserves text blocks and adds placeholder for image in same tool_result."""

    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_789",
                        "content": [
                            {"type": "text", "text": "Analysis complete."},
                            {
                                "type": "image",
                                "source": {"media_type": "image/jpeg", "data": "xyz"},
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results)
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    combined = " ".join(r.content for r in tool_results)
    assert "Analysis complete." in combined
    assert "[image:" in combined


def test_claude_parser_tool_result_with_audio_resource_reference_emits_placeholder() -> None:
    """Claude parser emits explicit [audio: URI] placeholder for audio resource_reference blocks.

    The placeholder must name the modality and URI so unattended workflows can
    diagnose modality loss without pretending the audio content was delivered.
    """

    parser = ClaudeParser()
    line = _json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_audio",
                        "content": [
                            {
                                "type": "resource_reference",
                                "uri": "ralph://media/audio-abc",
                                "modality": "audio",
                                "mimeType": "audio/mpeg",
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results), (
        f"Expected tool_result, got: {[r.type for r in results]}"
    )
    assert not any(r.type == "error" for r in results), (
        f"Got unexpected error: {[r for r in results if r.type == 'error']}"
    )
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[audio: ralph://media/audio-abc]" in r.content for r in tool_results), (
        f"Expected '[audio: ralph://media/audio-abc]' placeholder, "
        f"got: {[r.content for r in tool_results]}"
    )


def test_claude_parser_tool_result_with_video_resource_reference_emits_placeholder() -> None:
    """Claude parser emits explicit [video: URI] placeholder for video resource_reference blocks."""

    parser = ClaudeParser()
    line = _json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_video",
                        "content": [
                            {
                                "type": "resource_reference",
                                "uri": "ralph://media/video-xyz",
                                "modality": "video",
                                "mimeType": "video/mp4",
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results)
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[video: ralph://media/video-xyz]" in r.content for r in tool_results), (
        f"Expected '[video: ralph://media/video-xyz]' placeholder, "
        f"got: {[r.content for r in tool_results]}"
    )


def test_opencode_parser_tool_result_with_resource_reference_emits_placeholder() -> None:
    """OpenCode parser emits a modality placeholder for resource_reference in tool results.

    When read_media returns a resource_reference block (e.g. audio or video),
    the OpenCode parser must emit a bounded placeholder that names the modality
    and URI so unattended workflows can detect modality loss.
    """

    parser = OpenCodeParser()
    lines = [
        _json.dumps(
            {
                "type": "tool_result",
                "tool": "read_media",
                "result": [
                    {
                        "type": "resource_reference",
                        "uri": "ralph://media/aud-001",
                        "modality": "audio",
                        "mimeType": "audio/mpeg",
                    }
                ],
                "part": {"tool": "read_media", "input": {"path": "clip.mp3"}},
            }
        )
    ]
    results = list(parser.parse(_make_lines(lines)))
    assert any(r.type == "tool_result" for r in results), (
        f"Expected tool_result, got: {[r.type for r in results]}"
    )
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[audio: ralph://media/aud-001]" in r.content for r in tool_results), (
        f"Expected '[audio: ralph://media/aud-001]' placeholder, "
        f"got: {[r.content for r in tool_results]}"
    )
