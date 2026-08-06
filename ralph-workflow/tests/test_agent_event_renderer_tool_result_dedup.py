"""Regression coverage for B1: duplicated tool name on the live activity line.

Most AGY tools carry no ``tool_info.output`` on their DONE frame, so
``AgyParser._completion_summary`` synthesizes result content that already
begins with the tool name (``"write_to_file todo-list.js (0.08s)"``).
:func:`ralph.display.agent_event_renderer._render_tool_result_event`
separately prepends the tool name as its own segment before the body, so an
unmodified body doubled the tool name on the live activity line:

    write_to_file write_to_file todo-list.js (0.08s)

``_strip_duplicate_tool_prefix`` (called from ``_append_tool_result_body``)
removes exactly one leading duplicate occurrence so the name appears once.
"""

from __future__ import annotations

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.agent_event_renderer import render_event_kind_text


def test_tool_result_body_starting_with_tool_name_is_not_duplicated() -> None:
    """A parser-synthesized completion summary must not double the tool name."""
    line = render_event_kind_text(
        ActivityEventKind.TOOL_RESULT,
        "write_to_file todo-list.js (0.08s)",
        metadata={"tool_name": "write_to_file"},
    )

    assert "write_to_file write_to_file" not in line
    assert line.count("write_to_file") == 1
    assert "todo-list.js (0.08s)" in line


def test_tool_result_body_not_starting_with_tool_name_is_unchanged() -> None:
    """A body carrying real ``tool_info.output`` (e.g. view_file) is untouched."""
    line = render_event_kind_text(
        ActivityEventKind.TOOL_RESULT,
        "19 lines, 1395 bytes",
        metadata={"tool_name": "view_file"},
    )

    assert "view_file" in line
    assert "19 lines, 1395 bytes" in line
    assert line.count("view_file") == 1


def test_tool_result_body_exactly_equal_to_tool_name_collapses_to_bare_name() -> None:
    """A body that is nothing but the (duplicate) tool name leaves one occurrence."""
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
