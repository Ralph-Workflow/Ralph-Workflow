from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.artifact import handle_get_plan_draft, handle_submit_artifact
from ralph.mcp.tools.coordination import InvalidParamsError

if TYPE_CHECKING:
    from pathlib import Path
from tests.test_mcp_tool_artifact_helper__workspace import _Workspace

DEVELOPMENT_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"development_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)
REVIEW_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"review_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)



class _CapabilitySession:
    session_id = "session-1"

    def __init__(self, expected_capability: str) -> None:
        self.expected_capability = expected_capability

    def check_capability(self, capability: str) -> object:
        assert capability == self.expected_capability
        return "approved"




def test_submit_artifact_rejects_missing_content_source_with_actionable_guidance(
    tmp_path: Path,
) -> None:
    """When content is missing and workspace is available, error redirects to format doc."""
    session = _CapabilitySession("artifact.submit")
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
    session = _CapabilitySession("artifact.submit")
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


def test_get_plan_draft_uses_read_only_plan_capability(tmp_path: Path) -> None:
    session = _CapabilitySession("artifact.plan_read")
    workspace = _Workspace(tmp_path)

    result = handle_get_plan_draft(session, workspace, {})

    assert result.is_error is False
    assert result.content[0].text == json.dumps({"staged_sections": []})
