"""Additional production-path rendered-record regression tests."""

from __future__ import annotations

from pathlib import Path

from ralph.display.activity_event_kind import ActivityEventKind
from tests.display.test_raw_record_regression import _make_display_with_injected_clock


def test_subagent_progress_companion_does_not_duplicate_record_entry(
    tmp_path: Path,
) -> None:
    """DA-001: a TEXT event plus its progress companion produces one record entry."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    body = "Reading source files for the canonical entry path."
    pd.emit_parsed_event(unit_id="pi", kind=ActivityEventKind.TEXT, content=body, metadata={})
    pd.emit_parsed_event(
        unit_id="pi", kind=ActivityEventKind.SUBAGENT_PROGRESS, content=body, metadata={}
    )
    pd.stop()

    record_path = tmp_path / ".agent" / "raw" / "pi.rendered.log"
    assert record_path.exists()
    record_body = record_path.read_text(encoding="utf-8")
    assert "role=progress" not in record_body
    assert record_body.count(body) == 1


def test_source_event_timestamp_preserved_when_no_override(tmp_path: Path) -> None:
    """DA-002: a source timestamp survives the PresentedEntry boundary."""
    from ralph.display.activity_provider import ActivityProvider
    from ralph.display.agent_activity_event import AgentActivityEvent
    from ralph.display.presented_entry import build_presented_entry

    event = AgentActivityEvent(
        provider=ActivityProvider.PI,
        kind=ActivityEventKind.TEXT,
        content="hello world",
        metadata={},
        timestamp="2026-01-02T03:04:05+00:00",
    )
    assert build_presented_entry(event, unit_id="pi").timestamp == "2026-01-02T03:04:05+00:00"


def test_production_path_no_internal_channel_tokens_with_subagent_companion(
    tmp_path: Path,
) -> None:
    """DA-002: a progress companion cannot restore parser channel prefixes."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    for kind in (ActivityEventKind.TEXT, ActivityEventKind.SUBAGENT_PROGRESS):
        pd.emit_parsed_event(
            unit_id="pi", kind=kind, content="Working on the display redesign.", metadata={}
        )
    pd.stop()

    record_body = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    for forbidden in ("text:", "thinking:", "tool_use:", "tool_result:"):
        assert forbidden not in record_body


def test_production_path_two_distinct_identical_tool_use_events_both_recorded(
    tmp_path: Path,
) -> None:
    """DA-002: separate identical tool calls remain separate record entries."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    tool_body = "bash pytest tests/display -q"
    for _ in range(2):
        pd.emit_parsed_event(
            unit_id="pi",
            kind=ActivityEventKind.TOOL_USE,
            content=tool_body,
            metadata={"tool_name": "bash", "tool_path": None},
        )
    pd.stop()

    record_body = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    assert record_body.count(tool_body) == 2


def test_production_path_text_thinking_companion_deduped_on_live_and_record(
    tmp_path: Path,
) -> None:
    """DA-002: text and thinking companions surface once on both outputs."""
    pd, buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    body = "Session's master prompt: please read it carefully."
    for kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING):
        pd.emit_parsed_event(unit_id="pi", kind=kind, content=body, metadata={})
    pd.stop()

    record_body = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    assert record_body.count(body) == 1
    assert buf.getvalue().count(body) == 1
