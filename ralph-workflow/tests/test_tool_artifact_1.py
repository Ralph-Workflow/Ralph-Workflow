"""Canonical markdown handoff behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.handoffs import HANDOFF_PATHS
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from tests.test_artifact_format_docs_mock_session import planning_session
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_DOCUMENT = """---
type: development_result
status: completed
---
## Summary
- [S1] Markdown migration completed.
## Files Changed
- [F1] tests/test_tool_artifact_1.py
## Plan Items Proven
- [S-1] Focused tests pass.
"""


def test_submission_writes_byte_identical_artifact_and_handoff(tmp_path: Path) -> None:
    result = handle_submit_md_artifact(
        planning_session(drain="development"),
        MockWorkspace(tmp_path),
        {"artifact_type": "development_result", "content": _DOCUMENT},
    )

    artifact = tmp_path / ".agent" / "artifacts" / "development_result.md"
    handoff = tmp_path / HANDOFF_PATHS["development_result"]
    assert result.is_error is False
    assert artifact.read_text(encoding="utf-8") == _DOCUMENT
    assert handoff.read_text(encoding="utf-8") == _DOCUMENT
