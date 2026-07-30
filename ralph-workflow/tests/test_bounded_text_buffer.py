from __future__ import annotations

from loguru import logger

from ralph.agents._bounded_text_buffer import BoundedTextBuffer, clamp_tail


def test_clamp_tail_keeps_short_text_unchanged() -> None:
    text = "unchanged"

    assert clamp_tail(text, max_chars=len(text)) == text


def test_buffer_keeps_tail_and_counts_all_dropped_characters() -> None:
    buffer = BoundedTextBuffer(max_chars=4, label="test")

    buffer.append("abc")
    buffer.append("defgh")
    buffer.replace("ijklmn")

    assert buffer.value == "klmn"
    assert buffer.truncated_chars == 6


def test_buffer_warns_once_when_truncating() -> None:
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        buffer = BoundedTextBuffer(max_chars=2, label="test-buffer")
        buffer.append("abc")
        buffer.append("def")
    finally:
        logger.remove(sink_id)

    assert len(records) == 1
    assert "test-buffer" in records[0]
    assert "2" in records[0]
