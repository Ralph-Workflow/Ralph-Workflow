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
    pd.emit_parsed_event(unit_id="pi", kind=ActivityEventKind.TEXT, content="ALPHA", metadata={})
    pd.emit_phase_start("development_analysis", agent_name="pi")
    pd.emit_parsed_event(unit_id="pi", kind=ActivityEventKind.TEXT, content="BRAVO", metadata={})
    pd.stop()

    lines = (
        (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8").splitlines()
    )
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


def test_tool_call_record_omits_pair_marker_and_normalizes_call_shapes(tmp_path: Path) -> None:
    """DA-003: text-first calls have one shape and no live pairing residue."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    for content, metadata in (
        ("read", {"input": {"path": "a/b.py"}}),
        ("bash", {"input": {"command": "ls -la"}}),
        ("grep", {"input": {"pattern": "foo"}}),
    ):
        pd.emit_parsed_event(
            unit_id="pi", kind=ActivityEventKind.TOOL_USE, content=content, metadata=metadata
        )
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    calls = [line for line in record.splitlines() if "role=tool_call" in line]
    assert len(calls) == 3
    assert all("↳" not in line for line in calls)
    assert all(
        line.startswith("[09:30:00] ") and line.endswith(" role=tool_call") for line in calls
    )
    assert all(tool in line for tool, line in zip(("read", "bash", "grep"), calls, strict=True))


@pytest.mark.parametrize(
    ("content", "contains_pair_marker"),
    [("↳ read", False), ("read ↳", True), ("↳ read ↳", True)],
)
def test_tool_call_record_normalizes_only_leading_pair_marker(
    tmp_path: Path, content: str, contains_pair_marker: bool
) -> None:
    """DA-001 regression: pair chrome is stripped only from the leading position."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TOOL_USE,
        content=content,
        metadata={"input": {"path": "a/b.py"}},
    )
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    call = next(line for line in record.splitlines() if "role=tool_call" in line)
    assert "read" in call
    assert ("↳" in call) is contains_pair_marker
    assert call.count("↳") == int(contains_pair_marker)
    if contains_pair_marker:
        assert " ↳ role=tool_call" in call
        assert "↳ read" not in call


def test_event_rows_indent_beneath_phase_headers(tmp_path: Path) -> None:
    """Phase-owned record entries start below the header margin."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_phase_start("development", agent_name="pi")
    pd.emit_parsed_event(unit_id="pi", kind=ActivityEventKind.TOOL_USE, content="read", metadata={})
    pd.stop()

    lines = (
        (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8").splitlines()
    )
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
        if "[" in line:
            assert line.count("[") == 1


def test_agent_lifecycle_boundary_does_not_emit_empty_phase_header(tmp_path: Path) -> None:
    """DA-003: agent turn boundaries are not pipeline phase headers."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="pi", kind=ActivityEventKind.LIFECYCLE, content="turn_end", metadata={}
    )
    pd.stop()

    record_path = tmp_path / ".agent" / "raw" / "pi.rendered.log"
    assert not record_path.exists() or "role=phase_header" not in record_path.read_text(
        encoding="utf-8"
    )


def test_tool_call_replays_with_one_call_id_emit_once(tmp_path: Path) -> None:
    """DA-001: parser variants for one tool call share one record entry."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    for metadata in (
        {"tool_call_id": "call-1"},
        {"toolCall": {"id": "call-1"}},
        {"toolCallId": "call-1", "input": {"command": "pytest -q"}},
    ):
        pd.emit_parsed_event(
            unit_id="pi", kind=ActivityEventKind.TOOL_USE, content="exec", metadata=metadata
        )
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    assert len([line for line in record.splitlines() if "role=tool_call" in line]) == 1


def test_record_regression_status_and_unknown_omit_phase_identity_from_bodies(
    tmp_path: Path,
) -> None:
    """DA-002: status and fallback bodies never repeat their header identity."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_phase_start("development", agent_name="claude")
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.STATUS,
        content="claude Waiting for input.",
        metadata={},
    )
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.UNKNOWN,
        content="claude Unparsed line retained.",
        metadata={},
    )
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "claude.rendered.log").read_text(encoding="utf-8")
    assert record.count("claude") == 1
    assert "  [09:30:00] Waiting for input. role=status_line" in record
    assert "  [09:30:00] Unparsed line retained. role=unrecognized" in record


def test_result_text_companion_with_same_call_id_emits_once(tmp_path: Path) -> None:
    """DA-002: a tool output and its text echo have one condensation decision."""
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    for kind in (ActivityEventKind.TOOL_RESULT, ActivityEventKind.TEXT):
        pd.emit_parsed_event(
            unit_id="pi",
            kind=kind,
            content="payload from tool",
            metadata={"tool_call_id": "call-1", "tool_name": "read_file"},
        )
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    assert record.count("payload from tool") == 1
