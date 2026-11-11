from __future__ import annotations

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.agent_event_renderer import render_event_kind_text


def test_tool_result_body_starting_with_tool_name_is_not_duplicated() -> None:
    line = render_event_kind_text(
        ActivityEventKind.TOOL_RESULT,
        "write_to_file todo-list.js (0.08s)",
        metadata={"tool_name": "write_to_file"},
    )

    assert "write_to_file write_to_file" not in line
    assert line.count("write_to_file") == 1
    assert "todo-list.js (0.08s)" in line


def test_tool_result_body_not_starting_with_tool_name_is_unchanged() -> None:
    line = render_event_kind_text(
        ActivityEventKind.TOOL_RESULT,
        "19 lines, 1395 bytes",
        metadata={"tool_name": "view_file"},
    )

    assert "view_file" in line
    assert "19 lines, 1395 bytes" in line
    assert line.count("view_file") == 1


def test_tool_result_body_exactly_equal_to_tool_name_collapses_to_bare_name() -> None:
    line = render_event_kind_text(
        ActivityEventKind.TOOL_RESULT,
        "write_to_file",
        metadata={"tool_name": "write_to_file"},
    )

    assert line.count("write_to_file") == 1


def test_tool_result_dedup_is_case_insensitive() -> None:
    line = render_event_kind_text(
        ActivityEventKind.TOOL_RESULT,
        "Write_To_File todo-list.js (0.08s)",
        metadata={"tool_name": "write_to_file"},
    )

    assert line.casefold().count("write_to_file") == 1
