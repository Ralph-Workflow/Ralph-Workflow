"""OpenCode preview-shape regression coverage."""

from __future__ import annotations

import json

import pytest

from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.display.edit_preview import build_edit_preview
from ralph.display.preview_payload import payload_from_tool_event


@pytest.mark.parametrize(
    ("tool_name", "input_payload", "operation", "path", "old_text", "new_text"),
    (
        (
            "write",
            {"path": "src/example.py", "content": "x = 1\n"},
            "write",
            "src/example.py",
            "",
            "",
        ),
        (
            "edit",
            {"path": "src/example.py", "old_string": "x = 1", "new_string": "x = 2"},
            "replace",
            "src/example.py",
            "x = 1",
            "x = 2",
        ),
        (
            "ralph_mcp__ralph__write_file",
            {"path": "src/example.py", "content": "x = 1\n"},
            "write",
            "src/example.py",
            "",
            "",
        ),
    ),
)
def test_opencode_preview_regression_bare_file_tools_use_shared_preview_contract(
    tool_name: str,
    input_payload: dict[str, str],
    operation: str,
    path: str,
    old_text: str,
    new_text: str,
) -> None:
    """S-1: OpenCode bare write/edit events produce canonical previews."""
    payload = payload_from_tool_event(tool_name, {"input": input_payload})
    assert payload is not None
    assert payload.operation == operation
    assert payload.path == path
    assert build_edit_preview(tool_name, payload, width=80, terminal_bg_is_light=False) is not None
    assert build_edit_preview(tool_name, payload, width=80, terminal_bg_is_light=True) is not None
    if tool_name == "edit":
        assert len(payload.hunks) == 1
        assert payload.hunks[0].old_text == old_text
        assert payload.hunks[0].new_text == new_text


@pytest.mark.parametrize(
    ("tool_name", "input_payload", "expected_operation"),
    (
        ("write", {"path": "src/example.py", "content": "value = 1\n"}, "write"),
        (
            "edit",
            {
                "path": "src/example.py",
                "old_string": "value = 1",
                "new_string": "value = 2",
            },
            "replace",
        ),
    ),
)
def test_opencode_preview_regression_parser_native_state_input_uses_shared_contract(
    tool_name: str,
    input_payload: dict[str, str],
    expected_operation: str,
) -> None:
    """S-2: captured OpenCode state.input reaches the transport-neutral renderer."""
    raw = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": tool_name,
                "callID": f"call_{tool_name}",
                "state": {
                    "status": "completed",
                    "input": input_payload,
                    "output": "done",
                },
            },
        }
    )

    parsed = list(OpenCodeParser().parse(iter([raw])))

    assert [line.type for line in parsed] == ["tool_use", "tool_result"]
    payload = payload_from_tool_event(parsed[0].content, parsed[0].metadata)
    assert payload is not None
    assert payload.operation == expected_operation
    assert payload.path == "src/example.py"
    assert build_edit_preview(parsed[0].content, payload, width=80, terminal_bg_is_light=False)
    assert build_edit_preview(parsed[0].content, payload, width=80, terminal_bg_is_light=True)
    if tool_name == "edit":
        assert [(hunk.old_text, hunk.new_text) for hunk in payload.hunks] == [
            ("value = 1", "value = 2")
        ]
