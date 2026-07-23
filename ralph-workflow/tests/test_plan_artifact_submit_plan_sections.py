"""Incremental plan authoring through markdown staging tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ralph.mcp.tools.md_artifact import (
    handle_finalize_md_artifact,
    handle_get_md_draft,
    handle_stage_md_artifact,
)
from ralph.mcp.tools.tool_content import ToolContent
from ralph.workspace.fs import FsWorkspace
from tests.mcp.test_md_plan_spec import _plan_document
from tests.test_artifact_format_docs_mock_session import planning_session

_JSON_OBJECT = TypeAdapter(dict[str, object])

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination_session_like import CoordinationSessionLike
    from ralph.mcp.tools.tool_result import ToolResult


def _session() -> CoordinationSessionLike:
    return cast("CoordinationSessionLike", planning_session())


def _payload(result: ToolResult) -> dict[str, object]:
    block = result.content[0]
    assert isinstance(block, ToolContent)
    return _JSON_OBJECT.validate_json(block.text)


def test_plan_chunks_append_into_one_resumable_markdown_draft(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    document = _plan_document()
    split_at = document.index("## Steps")
    head, tail = document[:split_at], document[split_at:]

    first = handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": head},
    )
    second = handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": tail},
    )
    resumed = handle_get_md_draft(
        _session(), workspace, {"artifact_type": "plan"}
    )

    assert first.is_error is False
    assert _payload(first)["valid"] is False
    assert second.is_error is False
    assert _payload(second)["valid"] is True
    assert _payload(resumed)["content"] == document
    assert _payload(resumed)["sections"] == [
        "Summary",
        "Scope",
        "Skills MCP",
        "Steps",
        "Critical Files",
        "Constraints",
        "Design",
        "Acceptance Criteria",
        "Risks",
        "Verification",
    ]


def test_replace_all_repairs_a_staged_plan_before_finalization(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")
    handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": invalid},
    )

    rejected = handle_finalize_md_artifact(
        _session(), workspace, {"artifact_type": "plan"}
    )
    kept = handle_get_md_draft(
        _session(), workspace, {"artifact_type": "plan"}
    )
    handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": _plan_document(), "mode": "replace_all"},
    )
    finalized = handle_finalize_md_artifact(
        _session(), workspace, {"artifact_type": "plan"}
    )

    assert rejected.is_error is True
    assert _payload(kept)["content"] == invalid
    assert finalized.is_error is False
    assert (tmp_path / ".agent" / "artifacts" / "plan.md").read_text(
        encoding="utf-8"
    ) == _plan_document()
    after = handle_get_md_draft(
        _session(), workspace, {"artifact_type": "plan"}
    )
    assert _payload(after)["exists"] is False
