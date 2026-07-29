"""Live residual regressions for the shared rendered-record seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.display.activity_event_kind import ActivityEventKind
from tests.display.test_raw_record_regression import _make_display_with_injected_clock


def test_phase_header_record_uses_canonical_body_not_live_chrome(tmp_path: Path) -> None:
    """S-2: phase headers own readable phase context exactly once."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TEXT,
        content="first visible event",
        metadata={},
    )
    pd.emit_phase_start("development_commit", agent_name="pi")
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    headers = [line for line in record.splitlines() if "role=phase_header" in line]
    assert len(headers) == 1
    assert "Development Commit" in headers[0]
    assert "development_commit" not in headers[0]
    assert "phase_start" not in headers[0]
    assert "phase=" not in headers[0]
    assert chr(0x2139) + " INFO" not in headers[0]
    assert headers[0].count("agent=pi") == 1


def test_phase_change_closes_streamed_text_under_its_original_header(tmp_path: Path) -> None:
    """A streaming passage cannot cross a phase boundary in the record."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_phase_start("development", agent_name="pi")
    pd.emit_parsed_event(
        unit_id="pi", kind=ActivityEventKind.TEXT, content="ALPHA", metadata={}
    )
    pd.emit_phase_start("development_analysis", agent_name="pi")
    pd.emit_parsed_event(
        unit_id="pi", kind=ActivityEventKind.TEXT, content="BRAVO", metadata={}
    )
    pd.stop()

    lines = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(
        encoding="utf-8"
    ).splitlines()
    alpha = next(index for index, line in enumerate(lines) if "ALPHA" in line)
    analysis_header = next(
        index for index, line in enumerate(lines) if "Development Analysis" in line
    )
    bravo = next(index for index, line in enumerate(lines) if "BRAVO" in line)
    assert alpha < analysis_header < bravo
    assert all(not ("ALPHA" in line and "BRAVO" in line) for line in lines)


def test_tool_call_record_carries_args_once(tmp_path: Path) -> None:
    """S-1: the terminal call contributes one args-carrying record entry."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TOOL_USE,
        content="exec",
        metadata={"input": {"command": "pytest -q"}},
    )
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    calls = [line for line in record.splitlines() if "role=tool_call" in line]
    assert len(calls) == 1
    assert "command=pytest -q" in calls[0]


def test_event_rows_indent_beneath_phase_headers(tmp_path: Path) -> None:
    """Phase-owned record entries start below the header margin."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_phase_start("development", agent_name="pi")
    pd.emit_parsed_event(
        unit_id="pi", kind=ActivityEventKind.TOOL_USE, content="read", metadata={}
    )
    pd.stop()

    lines = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(
        encoding="utf-8"
    ).splitlines()
    header = next(index for index, line in enumerate(lines) if "role=phase_header" in line)
    assert lines[header + 1].startswith("  [")


@pytest.mark.parametrize(
    ("kind", "content"),
    [
        (ActivityEventKind.TEXT, "ordinary result body"),
        (ActivityEventKind.TOOL_RESULT, "completed tool action"),
    ],
)
def test_record_body_never_embeds_live_chrome(
    tmp_path: Path, kind: ActivityEventKind, content: str
) -> None:
    """Rendered record bodies exclude live badges and duplicate timestamps."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(unit_id="pi", kind=kind, content=content, metadata={})
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    entries = record.splitlines()
    assert any(content in line for line in entries)
    for line in entries:
        for chrome in (chr(0x2139) + " INFO", "◐ RUN", "✓ PASS", "⚠ WARN"):
            assert chrome not in line
        assert line.count("[") == 1
