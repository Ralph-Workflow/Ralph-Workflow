from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.artifact import handle_submit_artifact
from ralph.mcp.tools.coordination import InvalidParamsError

if TYPE_CHECKING:
    from pathlib import Path


DEVELOPMENT_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"development_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)
REVIEW_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"review_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)


class _Workspace:

    class _ApprovedSession:
        session_id = "session-1"

        def check_capability(self, capability: str) -> object:
            assert capability == "artifact.submit"
            return "approved"

    def __init__(self, root: Path) -> None:
        self._root = root

    def absolute_path(self, path: str) -> str:
        return str((self._root / path).resolve())


_ApprovedSession = _Workspace._ApprovedSession


def test_submit_artifact_rejects_missing_content_source_with_actionable_guidance(
    tmp_path: Path,
) -> None:
    """When content is missing and workspace is available, error redirects to format doc."""
    session = _ApprovedSession()
    workspace = _Workspace(tmp_path)

    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            session,
            workspace,
            {"artifact_type": "development_analysis_decision"},
        )

    message = str(exc_info.value)
    # When workspace is available, we redirect to the format doc
    # The redirect message includes the path to the format doc
    assert ".agent/artifact-formats/development_analysis_decision.md" in message


def test_submit_artifact_rejects_content_path_with_actionable_guidance(tmp_path: Path) -> None:
    """content_path is not part of the agent-facing contract and redirects to the format doc."""
    session = _ApprovedSession()
    workspace = _Workspace(tmp_path)

    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "review_analysis_decision",
                "content_path": ".agent/artifacts/review_analysis_decision.json",
            },
        )

    message = str(exc_info.value)
    assert ".agent/artifact-formats/review_analysis_decision.md" in message
