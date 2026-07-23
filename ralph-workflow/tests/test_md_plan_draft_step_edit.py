"""Black-box coverage for persisted Markdown plan-step editing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.coordination import ToolContent, ToolResult, WorkspaceLike
from ralph.mcp.tools.md_artifact import (
    handle_edit_md_plan_step,
    handle_get_md_draft,
    handle_stage_md_artifact,
)
from tests.mcp.test_md_plan_spec import _plan_document
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_session import planning_session


class _Workspace:
    def absolute_path(self, path: str) -> str:
        return str(Path("/workspace") / path)


def _payload(result: ToolResult) -> dict[str, object]:
    block = result.content[0]
    assert isinstance(block, ToolContent)
    return cast("dict[str, object]", json.loads(block.text))


def test_edit_plan_step_updates_the_persisted_draft_for_a_fresh_session() -> None:
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    workspace = cast("WorkspaceLike", _Workspace())
    handle_stage_md_artifact(
        planning_session(),
        workspace,
        {"artifact_type": "plan", "content": _plan_document()},
        deps=deps,
    )

    edited = handle_edit_md_plan_step(
        planning_session(),
        workspace,
        {
            "action": "replace",
            "step_id": "S-2",
            "replacement": (
                "### [S-2] Run the persisted draft suite\n"
                "Verify the persisted step edit.\n\n"
                "Type: verify\n"
                "Depends on: S-1\n"
                "Verify: pytest tests/test_md_plan_draft_step_edit.py -q\n"
            ),
        },
        deps=deps,
    )

    resumed = handle_get_md_draft(
        planning_session(),
        workspace,
        {"artifact_type": "plan"},
        deps=deps,
    )
    edited_payload = _payload(edited)
    resumed_payload = _payload(resumed)
    assert edited_payload["content"] == resumed_payload["content"]
    assert "### [S-2] Run the persisted draft suite" in cast(
        "str", resumed_payload["content"]
    )
    assert resumed_payload["valid"] is True
