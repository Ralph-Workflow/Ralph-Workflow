"""Markdown validator errors remain structured for tool clients."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.md_artifact import handle_verify_md_artifact
from tests.test_artifact_format_docs_mock_session import planning_session
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent


def test_validator_error_includes_rule_location_and_repair_message(tmp_path: Path) -> None:
    result = handle_verify_md_artifact(
        planning_session(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "commit_message",
            "content": "---\ntype: commit\nsubject: malformed\n---\n",
        },
    )

    payload = json.loads(cast("ToolContent", result.content[0]).text)
    diagnostics = cast("list[dict[str, object]]", payload["diagnostics"])
    error = next(item for item in diagnostics if item["severity"] == "error")
    assert error["rule_id"]
    assert isinstance(error["line"], int)
    assert "conventional commit format" in cast("str", error["message"])
