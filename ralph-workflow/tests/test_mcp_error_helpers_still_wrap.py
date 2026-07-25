"""Markdown validator errors remain structured for tool clients."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.tools.md_artifact import handle_verify_md_artifact
from tests._support.typed_accessors import (
    must_dict_list,
    must_str,
)
from tests.test_artifact_format_docs_mock_session import planning_session
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path



def test_validator_error_includes_rule_location_and_repair_message(tmp_path: Path) -> None:
    result = handle_verify_md_artifact(
        planning_session(),
        MockWorkspace(tmp_path),
        {
            "artifact_type": "commit_message",
            "content": "---\ntype: commit\nsubject: malformed\n---\n",
        },
    )

    payload = json.loads(result.content[0].text)
    diagnostics = must_dict_list(payload["diagnostics"])
    error = next(item for item in diagnostics if item["severity"] == "error")
    assert error["rule_id"]
    assert isinstance(error["line"], int)
    assert "conventional commit format" in must_str(error["message"])
