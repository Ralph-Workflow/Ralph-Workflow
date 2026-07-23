"""Black-box rollback contracts for markdown artifact submission."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from tests.test_artifact_format_docs_mock_session import planning_session
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_VALID = """---
type: commit
subject: fix(mcp): preserve canonical artifact
---
## Body
- [B1] Keep the last valid markdown document.
"""

_INVALID = """---
type: commit
subject: not conventional
---
"""


def test_invalid_submission_does_not_replace_last_valid_markdown(tmp_path: Path) -> None:
    session = planning_session()
    workspace = MockWorkspace(tmp_path)

    accepted = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "commit_message", "content": _VALID},
    )
    rejected = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "commit_message", "content": _INVALID},
    )

    artifact = tmp_path / ".agent" / "artifacts" / "commit_message.md"
    assert accepted.is_error is False
    assert rejected.is_error is True
    assert artifact.read_text(encoding="utf-8") == _VALID
