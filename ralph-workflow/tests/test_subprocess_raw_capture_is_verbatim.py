"""The subprocess executor's raw capture must be byte-faithful.

``sanitize_display_line`` exists to make a line safe to PRINT: it strips
terminal control sequences and truncates at 200 characters with a ``…``
suffix. That output was being written into ``.agent/raw/<id>.log``, the
file Ralph reads back as the agent's verbatim transcript and grades for
corruption.

Any wire frame longer than 200 characters therefore landed in the
capture as a severed JSON object -- ``{"type": "item.completed", "item":
{"result": "xxx…`` -- which is exactly the ``NON_JSONL`` shape
``detect_raw_log_breaks`` reports as ``raw transcript corrupted``. A
Codex or Claude frame carrying a tool result is routinely far longer
than 200 characters, so the capture was being truncated into garbage by
the very layer that claims to preserve it.

The display still gets the sanitized line; only the capture gets the
original bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.display.raw_overflow import RawOverflowLog, detect_raw_log_breaks

pytestmark = pytest.mark.timeout_seconds(5)


def _long_wire_frame() -> str:
    """A realistic JSONL frame comfortably past the 200-char display cap."""
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"id": "item_18", "type": "mcp_tool_call", "result": "Z" * 600},
        }
    )


def test_sanitizer_truncation_would_corrupt_a_wire_frame() -> None:
    """Pin the hazard this guards, so the rationale cannot rot."""
    from ralph.display.line_sanitizer import sanitize_display_line

    frame = _long_wire_frame()
    sanitized = sanitize_display_line(frame)

    assert sanitized != frame
    with pytest.raises(json.JSONDecodeError):
        json.loads(sanitized)


def test_verbatim_capture_keeps_the_full_wire_frame(tmp_path: Path) -> None:
    """A frame written to the capture must parse back as JSON."""
    frame = _long_wire_frame()
    log = RawOverflowLog(tmp_path, "codex")
    try:
        log.append(frame)
        log.flush()
        written = log.path.read_text(encoding="utf-8").strip()
    finally:
        log.close()

    assert json.loads(written)["item"]["result"] == "Z" * 600
    assert detect_raw_log_breaks(log.path) == []
