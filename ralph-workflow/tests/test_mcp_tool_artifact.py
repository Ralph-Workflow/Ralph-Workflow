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
CONTENT_PATH_GUIDANCE = (
    "Use 'content_path' only when resubmitting a JSON file that already exists on disk."
)


class _ApprovedSession:
    session_id = "session-1"

    def check_capability(self, capability: str) -> object:
        assert capability == "artifact.submit"
        return "approved"


class _Workspace:
    def __init__(self, root: Path) -> None:
        self._root = root

    def absolute_path(self, path: str) -> str:
        return str((self._root / path).resolve())


def test_submit_artifact_rejects_missing_content_source_with_actionable_guidance(
    tmp_path: Path,
) -> None:
    session = _ApprovedSession()
    workspace = _Workspace(tmp_path)

    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            session,
            workspace,
            {"artifact_type": "development_analysis_decision"},
        )

    message = str(exc_info.value)
    assert "Provide exactly one of 'content' or 'content_path'" in message
    assert DEVELOPMENT_FRESH_SUBMIT_EXAMPLE in message
    assert "Use 'content' for a freshly generated JSON string." in message
    assert CONTENT_PATH_GUIDANCE in message


def test_submit_artifact_rejects_both_content_and_content_path_with_actionable_guidance(
    tmp_path: Path,
) -> None:
    session = _ApprovedSession()
    workspace = _Workspace(tmp_path)

    with pytest.raises(InvalidParamsError) as exc_info:
        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "review_analysis_decision",
                "content": '{"status":"completed","summary":"ok"}',
                "content_path": ".agent/artifacts/review_analysis_decision.json",
            },
        )

    message = str(exc_info.value)
    assert "Provide exactly one of 'content' or 'content_path'" in message
    assert REVIEW_FRESH_SUBMIT_EXAMPLE in message
    assert "Never send both 'content' and 'content_path' in the same call." in message
