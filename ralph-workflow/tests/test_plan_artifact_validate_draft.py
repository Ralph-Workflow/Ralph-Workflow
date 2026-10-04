"""Read-only inspection of staged plan prose through public tools."""

from __future__ import annotations

import json

from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import (
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_verify_md_artifact,
)
from ralph.workspace.memory import MemoryWorkspace
from tests._artifact_format_docs_mock_session import planning_session
from tests._tool_artifact_2_helper_memorybackend import MemoryBackend

_PLAN = "Inspect the repository then implement independent changes and verify their combined behavior."


def test_verify_prose_plan_is_valid_without_persisting_it() -> None:
    workspace = MemoryWorkspace()
    result = handle_verify_md_artifact(
        planning_session(), workspace, {"artifact_type": "plan", "content": _PLAN}
    )
    payload = json.loads(result.content[0].text)
    assert not result.is_error
    assert payload["valid"] is True
    assert payload["diagnostics"] == []
    assert not workspace.exists(".agent/artifacts/plan.md")


def test_staged_draft_is_readable_and_unchanged_across_repeated_inspections() -> None:
    workspace = MemoryWorkspace()
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    session = planning_session()
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "plan", "content": _PLAN}, deps=deps
    )
    first = handle_get_md_draft(session, workspace, {"artifact_type": "plan"}, deps=deps)
    second = handle_get_md_draft(session, workspace, {"artifact_type": "plan"}, deps=deps)
    assert json.loads(first.content[0].text) == json.loads(second.content[0].text)
    assert json.loads(first.content[0].text)["content"] == _PLAN
    assert json.loads(first.content[0].text)["valid"] is True
    assert not backend.exists(workspace.root / ".agent/artifacts/plan.md")
