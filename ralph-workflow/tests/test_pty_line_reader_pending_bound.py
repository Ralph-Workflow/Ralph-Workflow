from __future__ import annotations

from ralph.agents._bounded_text_buffer import DEFAULT_MAX_BUFFER_CHARS, clamp_tail


def test_overlong_pending_tail_keeps_newest_newline_terminated_line() -> None:
    pending = clamp_tail("x" * (DEFAULT_MAX_BUFFER_CHARS + 1), max_chars=DEFAULT_MAX_BUFFER_CHARS)
    pending = clamp_tail(pending + "complete line\n", max_chars=DEFAULT_MAX_BUFFER_CHARS)

    assert pending.endswith("complete line\n")
    assert len(pending) == DEFAULT_MAX_BUFFER_CHARS
