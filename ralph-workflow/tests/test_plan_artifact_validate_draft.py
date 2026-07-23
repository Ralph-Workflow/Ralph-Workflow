"""Read-only validation coverage for staged plan markdown."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ralph.mcp.tools.md_artifact import (
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_verify_md_artifact,
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


def test_verify_complete_plan_is_valid_without_persisting_it(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    result = handle_verify_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": _plan_document()},
    )

    assert result.is_error is False
    assert _payload(result) == {
        "artifact_type": "plan",
        "valid": True,
        "diagnostics": [],
    }
    assert not (tmp_path / ".agent" / "artifacts" / "plan.md").exists()


def test_get_draft_reports_cross_reference_error_without_mutating_content(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")
    handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": invalid},
    )

    first = _payload(
        handle_get_md_draft(
            _session(), workspace, {"artifact_type": "plan"}
        )
    )
    second = _payload(
        handle_get_md_draft(
            _session(), workspace, {"artifact_type": "plan"}
        )
    )

    diagnostics = cast("list[dict[str, object]]", first["diagnostics"])
    assert first["valid"] is False
    assert any(item["rule_id"] == "PLAN021" for item in diagnostics)
    assert first["content"] == invalid
    assert second["content"] == invalid
    assert second["diagnostics"] == diagnostics
