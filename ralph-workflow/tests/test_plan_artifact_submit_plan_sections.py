"""Incremental plan authoring through markdown staging tools."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ralph.mcp.artifacts.md_draft_io import delete_md_draft
from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.mcp.tools.md_artifact import (
    handle_finalize_md_artifact,
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
)
from ralph.mcp.tools.tool_content import ToolContent
from ralph.pipeline.phase_entry_cleaner import clear_phase_entry_drains
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from tests._artifact_format_docs_mock_session import planning_session
from tests.mcp.test_md_plan_spec import _plan_document

_JSON_OBJECT = TypeAdapter(dict[str, object])

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination_session_like import CoordinationSessionLike
    from ralph.mcp.tools.tool_result import ToolResult


def _session() -> CoordinationSessionLike:
    return planning_session()


def _payload(result: ToolResult) -> dict[str, object]:
    block = result.content[0]
    assert isinstance(block, ToolContent)
    return _JSON_OBJECT.validate_json(block.text)


def test_plan_chunks_append_into_one_resumable_markdown_draft(tmp_path: Path) -> None:
    """Two staging chunks that assemble a real plan must report a valid resume.

    Under the plan-scoped severity policy, a partial plan (head + tail)
    that still produces a complete plan is valid: warnings are advisory.
    The first chunk is incomplete and so reports invalid, the second
    completes the document and reports valid. The resumed draft
    contains the full plan text and the canonical section list.
    """
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
    resumed = handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"})

    assert first.is_error is False
    # The head-only draft lacks step blocks, which is a PLAN022 warning
    # under the plan-scoped severity policy. The chunk still validates
    # as a plan (advisory demotion, not blocking). The tail chunk adds
    # the step body and the assembled plan is valid.
    assert _payload(first)["valid"] is True
    first_diagnostics = _payload(first).get("diagnostics", [])
    assert any(
        diagnostic.get("rule_id") == "PLAN022" and diagnostic.get("severity") == "warning"
        for diagnostic in first_diagnostics
    )
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


def test_plan_regression_seeded_draft_rejects_default_append(tmp_path: Path) -> None:
    """S-4: restored canonical plans cannot silently concatenate with a new draft."""
    workspace = FsWorkspace(tmp_path)
    session = _session()
    plan_a = _plan_document()
    submitted = handle_submit_md_artifact(
        session, workspace, {"artifact_type": "plan", "content": plan_a}
    )
    assert submitted.is_error is False

    artifact_dir = tmp_path / ".agent" / "artifacts"
    assert delete_md_draft(artifact_dir, "plan") is True
    defaults = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    artifacts_policy = load_policy(defaults).artifacts
    pipeline_policy = load_policy(defaults).pipeline
    clear_phase_entry_drains(
        workspace,
        "planning",
        "planning_analysis",
        pipeline_policy,
        artifacts_policy,
    )

    with pytest.raises(InvalidParamsError, match="replace_all"):
        handle_stage_md_artifact(
            session, workspace, {"artifact_type": "plan", "content": "new plan"}
        )
    assert (
        _payload(handle_get_md_draft(session, workspace, {"artifact_type": "plan"}))["content"]
        == plan_a
    )


def test_replace_all_repairs_a_staged_plan_before_finalization(tmp_path: Path) -> None:
    """A warnings-only plan finalizes; a repaired replacement finalizes cleanly.

    Under the plan-scoped severity policy, a dangling ``Depends on:``
    is a PLAN021 warning, not a blocking error. The plan still finalizes
    (the warning lives in the diagnostic list) and the replace_all
    step repairs the warning before the second finalize.
    """
    workspace = FsWorkspace(tmp_path)
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")
    handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": invalid},
    )

    rejected = handle_finalize_md_artifact(_session(), workspace, {"artifact_type": "plan"})
    kept = handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"})
    handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": _plan_document(), "mode": "replace_all"},
    )
    finalized = handle_finalize_md_artifact(_session(), workspace, {"artifact_type": "plan"})

    assert rejected.is_error is False
    rejected_payload = _payload(rejected)
    assert any(
        diagnostic.get("rule_id") == "PLAN021" and diagnostic.get("severity") == "warning"
        for diagnostic in rejected_payload.get("diagnostics", [])
    )
    assert _payload(kept)["content"] == invalid
    assert finalized.is_error is False
    assert (tmp_path / ".agent" / "artifacts" / "plan.md").read_text(
        encoding="utf-8"
    ) == _plan_document()
    # The finalized document is retained as the draft so it stays editable for
    # an in-phase revision; only fresh phase entry clears it.
    after = handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"})
    assert _payload(after)["exists"] is True
    assert _payload(after)["content"] == _plan_document()
