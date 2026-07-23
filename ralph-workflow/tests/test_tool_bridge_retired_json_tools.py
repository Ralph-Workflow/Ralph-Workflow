"""Retired JSON artifact tools must fail with an error naming the markdown replacement.

CONTRACT (PROMPT.md): the markdown artifact migration removed the JSON artifact
MCP tools. An agent calling a removed tool name gets a clear error that says the
tool was removed in the markdown artifact migration and names the specific
markdown replacement to call instead — while keeping the ``is not registered``
phrasing the recovery classifier keys on.
"""

from __future__ import annotations

import pytest

from ralph.mcp.tools.bridge._tool_bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_dispatch_error import ToolDispatchError

# The retired JSON tool names and their markdown replacements, pinned as
# literals so a drifting mapping in the bridge fails this test.
_EXPECTED_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("ralph_submit_artifact", "ralph_submit_md_artifact"),
    ("ralph_submit_plan_section", "ralph_stage_md_artifact"),
    ("ralph_submit_plan_sections", "ralph_stage_md_artifact"),
    ("ralph_finalize_plan", "ralph_finalize_md_artifact"),
    ("ralph_get_plan_draft", "ralph_get_md_draft"),
    ("ralph_discard_plan_draft", "ralph_discard_md_draft"),
    ("ralph_validate_draft", "ralph_verify_md_artifact"),
    ("ralph_patch_step", "ralph_edit_md_plan_step"),
    ("ralph_insert_plan_step", "ralph_edit_md_plan_step"),
    ("ralph_replace_plan_step", "ralph_edit_md_plan_step"),
    ("ralph_remove_plan_step", "ralph_edit_md_plan_step"),
    ("ralph_move_plan_step", "ralph_edit_md_plan_step"),
)


def _get_error_message(name: str) -> str:
    bridge = ToolBridge()
    with pytest.raises(ToolDispatchError) as exc_info:
        bridge.get(name)
    return str(exc_info.value)


@pytest.mark.parametrize(("retired", "replacement"), _EXPECTED_REPLACEMENTS)
def test_retired_json_tool_names_markdown_replacement(retired: str, replacement: str) -> None:
    message = _get_error_message(retired)
    assert "is not registered" in message  # recovery classifier contract
    assert "removed" in message
    assert "markdown artifact migration" in message
    assert f"'{replacement}'" in message  # tells the agent what to actually call


@pytest.mark.parametrize(
    "prefixed",
    [
        "mcp__ralph__ralph_submit_artifact",
        "ralph_mcp__ralph_submit_artifact",
        "ralph__ralph_submit_artifact",
        "ralph.ralph_submit_artifact",
    ],
)
def test_retired_tool_with_invented_prefix_names_replacement(prefixed: str) -> None:
    """Mis-prefixed retired names get the same removal error as the bare name."""
    message = _get_error_message(prefixed)
    assert "is not registered" in message
    assert "markdown artifact migration" in message
    assert "'ralph_submit_md_artifact'" in message


def test_retired_tool_message_exact_shape() -> None:
    message = _get_error_message("ralph_submit_artifact")
    assert message == (
        "Tool 'ralph_submit_artifact' is not registered: it was removed in the "
        "markdown artifact migration. Call 'ralph_submit_md_artifact' instead."
    )


def test_non_retired_unknown_tool_keeps_generic_message() -> None:
    """The retired-tool path must not swallow ordinary unknown-tool errors."""
    message = _get_error_message("ralph_totally_unknown")
    assert "is not registered" in message
    assert "Available tools" in message
    assert "markdown artifact migration" not in message
