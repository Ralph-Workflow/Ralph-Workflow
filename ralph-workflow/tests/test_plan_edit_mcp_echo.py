"""Wire-result coverage for markdown plan-step edits."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import TypeAdapter

from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    WorkspaceLike,
)
from ralph.mcp.tools.md_artifact import handle_edit_md_plan_step
from tests.mcp.test_md_plan_spec import _plan_document
from tests.test_artifact_format_docs_mock_session import planning_session

_JSON_OBJECT = TypeAdapter(dict[str, object])


def _session() -> CoordinationSessionLike:
    return cast("CoordinationSessionLike", planning_session())


def _workspace() -> WorkspaceLike:
    return cast("WorkspaceLike", None)


def test_edit_tool_echoes_only_the_updated_markdown_document() -> None:
    result = handle_edit_md_plan_step(
        _session(),
        _workspace(),
        {
            "content": _plan_document(),
            "action": "replace",
            "step_id": "S-2",
            "replacement": (
                "### [S-2] Run focused tests\n"
                "Run the focused migration tests.\n\n"
                "Type: verify\n"
                "Depends on: S-1\n"
                "Verify: pytest tests/test_plan_artifact*.py -q\n"
            ),
        },
    )

    block = result.content[0]
    assert isinstance(block, ToolContent)
    payload = _JSON_OBJECT.validate_json(block.text)
    edited = payload["content"]
    assert isinstance(edited, str)
    assert set(payload) == {"content"}
    assert "### [S-2] Run focused tests" in edited
    assert "Depends on: S-1" in edited


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"action": "move", "step_id": "S-1"}, "content, action, and step_id"),
        (
            {
                "content": "x",
                "action": "replace",
                "step_id": "S-1",
                "replacement": {"title": "JSON is retired"},
            },
            "replacement must be a markdown step block",
        ),
        (
            {
                "content": "x",
                "action": "move",
                "step_id": "S-1",
                "index": "1",
            },
            "index must be an integer",
        ),
    ],
)
def test_edit_tool_rejects_non_markdown_wire_shapes(
    params: dict[str, object], message: str
) -> None:
    with pytest.raises(InvalidParamsError, match=message):
        handle_edit_md_plan_step(
            _session(),
            _workspace(),
            params,
        )
