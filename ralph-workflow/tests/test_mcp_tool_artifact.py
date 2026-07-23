"""Public markdown artifact handler contracts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact, handle_verify_md_artifact
from tests.test_artifact_format_docs_mock_session import MockSession, planning_session
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent

_DOCUMENT = """---
type: commit
subject: test(mcp): cover markdown submission
---
## Body
- [B1] Exercise the public markdown handler.
"""


class _RunSession(MockSession):
    run_id = "run-md-submit"


def test_submit_writes_canonical_markdown_and_receipt(tmp_path: Path) -> None:
    session = _RunSession(session_id="run-md-submit")
    result = handle_submit_md_artifact(
        session,
        MockWorkspace(tmp_path),
        {"artifact_type": "commit_message", "content": _DOCUMENT},
    )

    assert result.is_error is False
    assert (
        tmp_path / ".agent" / "artifacts" / "commit_message.md"
    ).read_text(encoding="utf-8") == _DOCUMENT
    assert artifact_receipt_present(tmp_path, "run-md-submit", "commit_message")


def test_verify_returns_structured_diagnostics_without_writing(tmp_path: Path) -> None:
    result = handle_verify_md_artifact(
        planning_session(),
        MockWorkspace(tmp_path),
        {"artifact_type": "commit_message", "content": "---\ntype: commit\n---\n"},
    )

    payload = json.loads(cast("ToolContent", result.content[0]).text)
    diagnostics = cast("list[dict[str, object]]", payload["diagnostics"])
    assert result.is_error is True
    assert diagnostics
    assert {"line", "section", "rule_id", "severity"} <= diagnostics[0].keys()
    assert not (tmp_path / ".agent" / "artifacts" / "commit_message.md").exists()


def test_submit_requires_markdown_content_parameter(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="content"):
        handle_submit_md_artifact(
            planning_session(),
            MockWorkspace(tmp_path),
            {"artifact_type": "commit_message"},
        )
