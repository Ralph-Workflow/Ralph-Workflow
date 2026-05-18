"""Tests for the typed activity model display helpers."""

from __future__ import annotations

import threading
from datetime import datetime

from rich.cells import cell_len

from ralph.display import activity_model
from ralph.display.activity_model import (
    ActivityEventKind,
    ActivityProvider,
    SequenceCounter,
    make_event,
    render_event_line,
)

THREAD_COUNT = 10
CALLS_PER_THREAD = 100
EXPECTED_SEQUENCES = THREAD_COUNT * CALLS_PER_THREAD
WIDE_CONTENT_CELLS = 202
TRUNCATED_CONTENT_CELLS = 201
TRUNCATED_SMILEYS = 100


def test_make_event_returns_sequence_and_timestamp() -> None:
    activity_model.module_sequence = SequenceCounter()

    event = make_event(provider=ActivityProvider.CLAUDE, kind=ActivityEventKind.TEXT)

    assert event.sequence is not None and event.sequence >= 1
    assert event.timestamp is not None
    datetime.fromisoformat(event.timestamp)


def test_make_event_sequences_strictly_increase() -> None:
    activity_model.module_sequence = SequenceCounter()

    first = make_event(provider=ActivityProvider.CLAUDE, kind=ActivityEventKind.TEXT)
    second = make_event(provider=ActivityProvider.CLAUDE, kind=ActivityEventKind.TEXT)

    assert first.sequence is not None
    assert second.sequence is not None
    assert first.sequence < second.sequence


def test_make_event_is_thread_safe_for_unique_sequences() -> None:
    activity_model.module_sequence = SequenceCounter()

    sequences: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(CALLS_PER_THREAD):
            event = make_event(provider=ActivityProvider.CLAUDE, kind=ActivityEventKind.TEXT)
            assert event.sequence is not None
            with lock:
                sequences.append(event.sequence)

    threads = [threading.Thread(target=worker) for _ in range(THREAD_COUNT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(sequences) == EXPECTED_SEQUENCES
    assert len(set(sequences)) == EXPECTED_SEQUENCES
    assert min(sequences) == 1
    assert max(sequences) == EXPECTED_SEQUENCES


def test_render_event_line_escapes_markup() -> None:
    rendered = render_event_line(
        ActivityEventKind.TEXT,
        "[red]injected[/red]",
        timestamp="2026-04-18T12:00:00+00:00",
    )

    assert "\\[red]injected\\[/red]" in rendered


def test_render_event_line_uses_tool_use_icon() -> None:
    rendered = render_event_line(
        ActivityEventKind.TOOL_USE,
        "bash",
        timestamp="2026-04-18T12:00:00+00:00",
    )

    assert "▸" in rendered


def test_render_event_line_truncates_to_200_cells() -> None:
    content = "🙂" * 101
    assert cell_len(content) == WIDE_CONTENT_CELLS

    rendered = render_event_line(
        ActivityEventKind.TEXT,
        content,
        timestamp="2026-04-18T12:00:00+00:00",
    )

    rendered_content = rendered.split("[/] ", 1)[1]
    assert cell_len(rendered_content) == TRUNCATED_CONTENT_CELLS
    assert rendered_content.endswith("…")
    assert rendered_content.count("🙂") == TRUNCATED_SMILEYS
