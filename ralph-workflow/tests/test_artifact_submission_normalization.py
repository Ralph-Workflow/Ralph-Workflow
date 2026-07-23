"""Markdown submission rejects legacy JSON instead of repairing it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from tests.test_artifact_format_docs_mock_session import planning_session
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent


def test_submit_rejects_legacy_json_without_writing_an_artifact(tmp_path: Path) -> None:
    result = handle_submit_md_artifact(
        planning_session(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "commit_message",
            "content": '{"type":"commit","subject":"fix(mcp): old transport"}',
        },
    )

    payload = json.loads(cast("ToolContent", result.content[0]).text)
    diagnostics = cast("list[dict[str, object]]", payload["diagnostics"])
    assert result.is_error is True
    assert any(item["severity"] == "error" for item in diagnostics)
    assert not (tmp_path / ".agent" / "artifacts" / "commit_message.md").exists()
