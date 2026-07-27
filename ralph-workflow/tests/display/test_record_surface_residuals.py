"""Live residual regressions for the shared rendered-record seam."""

from __future__ import annotations

from pathlib import Path

from ralph.display.activity_event_kind import ActivityEventKind
from tests.display.test_raw_record_regression import _make_display_with_injected_clock


def test_phase_header_record_uses_canonical_body_not_live_chrome(tmp_path: Path) -> None:
    """S-1: phase headers retain canonical lifecycle text exactly once."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TEXT,
        content="first visible event",
        metadata={},
    )
    pd.emit_phase_start("development", agent_name="pi")
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    headers = [line for line in record.splitlines() if "role=phase_header" in line]
    assert len(headers) == 1
    assert "phase_start phase=development" in headers[0]
    assert "\N{INFORMATION SOURCE} INFO" not in headers[0]
    assert headers[0].count("agent=pi") == 1


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
