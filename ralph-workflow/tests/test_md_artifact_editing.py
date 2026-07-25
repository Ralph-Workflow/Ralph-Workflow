"""Anchored oldText/newText editing of persisted markdown artifact drafts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from ralph.config.mcp_models import McpConfig
from ralph.mcp.tools._side_effects import REGISTRY
from ralph.mcp.tools.bridge import tool_specs
from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.md_artifact import (
    handle_edit_md_artifact,
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
)
from ralph.mcp.tools.names import EDIT_MD_ARTIFACT_TOOL
from tests._support.typed_accessors import must_mapping

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import ToolResult, WorkspaceLike


class MockSession:
    """Typed stand-in satisfying CoordinationSessionLike for handler tests."""

    session_id: str = "test-session"
    #: Empty run id keeps canonical submission off the receipt/sentinel path.
    run_id: str = ""
    explore_index: object | None = None

    @property
    def broker_secret(self) -> str | None:
        return None

    def check_capability(self, capability: str) -> object:
        return capability in {"artifact.submit", "artifact.plan_read"}


def _workspace(root: Path) -> WorkspaceLike:
    """Return a typed workspace stand-in rooted at ``root``."""

    class Workspace:
        def absolute_path(self, path: str) -> str:
            return str(root / path)

    return Workspace()


_SPEC = """---
type: product_spec
---
## Title
- [T1] Markdown artifacts
## Scope
- [S1] Move artifacts to markdown
## Goals
- [G1] Reduce authoring friction
## Users
- [U1] Agents
## Success Criteria
- [C1] Markdown validates
"""


def _payload(result: ToolResult) -> dict[str, object]:
    content = result.content[0]
    return must_mapping(json.loads(content.text))


def _draft(session: MockSession, workspace: WorkspaceLike) -> str:
    payload = _payload(handle_get_md_draft(session, workspace, {"artifact_type": "product_spec"}))
    return str(payload["content"])


def _edit(
    session: MockSession,
    workspace: WorkspaceLike,
    edits: list[dict[str, str]],
    **extra: object,
) -> ToolResult:
    params: dict[str, object] = {"artifact_type": "product_spec", "edits": edits}
    params.update(extra)
    return handle_edit_md_artifact(session, workspace, params)


def test_edit_replaces_the_first_occurrence_and_persists_the_draft(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )

    result = _edit(session, workspace, [{"oldText": "Agents", "newText": "Coding agents"}])

    assert result.is_error is False
    payload = _payload(result)
    assert payload["status"] == "applied"
    assert payload["edits_applied"] == 1
    assert "Coding agents" in str(payload["diff"])
    assert _draft(session, workspace) == _SPEC.replace("Agents", "Coding agents")


def test_edit_applies_multiple_edits_sequentially(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )

    result = _edit(
        session,
        workspace,
        [
            {"oldText": "Reduce authoring friction", "newText": "Reduce revision cost"},
            {"oldText": "Reduce revision cost", "newText": "Reduce revision cost sharply"},
        ],
    )

    assert _payload(result)["edits_applied"] == 2
    assert "Reduce revision cost sharply" in _draft(session, workspace)


def test_edit_reports_the_refreshed_artifact_diagnostics(tmp_path: Path) -> None:
    """One response tells the agent what changed and whether it now validates."""
    session = MockSession()
    workspace = _workspace(tmp_path)
    broken = _SPEC.replace("## Success Criteria\n- [C1] Markdown validates\n", "")
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": broken}
    )

    before = _payload(_edit(session, workspace, [{"oldText": "Agents", "newText": "Agents!"}]))
    assert before["valid"] is False

    after = _payload(
        _edit(
            session,
            workspace,
            [
                {
                    "oldText": "- [U1] Agents!\n",
                    "newText": "- [U1] Agents\n## Success Criteria\n- [C1] Markdown validates\n",
                }
            ],
        )
    )
    assert after["valid"] is True
    assert after["sections"] == ["Title", "Scope", "Goals", "Users", "Success Criteria"]


def test_edit_aborts_the_whole_batch_when_one_edit_does_not_match(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )

    result = _edit(
        session,
        workspace,
        [
            {"oldText": "Agents", "newText": "Coding agents"},
            {"oldText": "nowhere in the document", "newText": "x"},
        ],
    )

    assert result.is_error is True
    payload = _payload(result)
    assert payload["status"] == "no_match"
    assert payload["edit_index"] == 1
    # Nothing is written: the earlier edit in the batch is rolled back with it.
    assert _draft(session, workspace) == _SPEC


def test_edit_dry_run_previews_without_writing(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )

    result = _edit(
        session,
        workspace,
        [{"oldText": "Agents", "newText": "Coding agents"}],
        dry_run=True,
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["status"] == "preview"
    assert "Coding agents" in str(payload["diff"])
    assert _draft(session, workspace) == _SPEC


def test_edit_fails_closed_on_a_stale_expected_content_hash(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )
    stale = hashlib.sha256(b"a different draft").hexdigest()

    result = _edit(
        session,
        workspace,
        [{"oldText": "Agents", "newText": "Coding agents"}],
        expected_content_hash=stale,
    )

    assert result.is_error is True
    payload = _payload(result)
    assert payload["status"] == "stale_evidence"
    assert payload["reason"] == "content_changed"
    assert _draft(session, workspace) == _SPEC


def test_edit_accepts_a_matching_expected_content_hash(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )
    current = hashlib.sha256(_SPEC.encode("utf-8")).hexdigest()

    result = _edit(
        session,
        workspace,
        [{"oldText": "Agents", "newText": "Coding agents"}],
        expected_content_hash=current,
    )

    assert result.is_error is False
    assert _payload(result)["status"] == "applied"


def test_edit_without_a_draft_is_an_invalid_params_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="no staged draft"):
        _edit(MockSession(), _workspace(tmp_path), [{"oldText": "a", "newText": "b"}])


def test_edit_rejects_a_missing_or_empty_edits_list(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )

    with pytest.raises(InvalidParamsError, match="edits"):
        _edit(session, workspace, [])
    with pytest.raises(InvalidParamsError, match="oldText"):
        _edit(session, workspace, [{"newText": "b"}])
    with pytest.raises(InvalidParamsError, match="oldText"):
        _edit(session, workspace, [{"oldText": "", "newText": "b"}])


def test_edit_enforces_the_draft_character_cap(tmp_path: Path) -> None:
    session = MockSession()
    workspace = _workspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _SPEC}
    )

    with pytest.raises(InvalidParamsError, match="character cap"):
        _edit(session, workspace, [{"oldText": "Agents", "newText": "x" * 4_000_001}])

    assert _draft(session, workspace) == _SPEC


def test_edit_repairs_a_rejected_whole_document_submission(tmp_path: Path) -> None:
    """The end-to-end flow the validation-error pointer advertises."""
    session = MockSession()
    workspace = _workspace(tmp_path)
    broken = _SPEC.replace("- [C1] Markdown validates\n", "")

    rejected = handle_submit_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": broken}
    )
    assert rejected.is_error is True

    repaired = _edit(
        session,
        workspace,
        [
            {
                "oldText": "## Success Criteria\n",
                "newText": "## Success Criteria\n- [C1] Markdown validates\n",
            }
        ],
    )

    assert _payload(repaired)["valid"] is True
    assert _draft(session, workspace) == _SPEC


def test_edit_tool_is_registered_with_a_mutate_side_effect_contract() -> None:
    tool_names = {spec.metadata.definition.name for spec in tool_specs(McpConfig())}

    assert EDIT_MD_ARTIFACT_TOOL in tool_names
    assert REGISTRY[EDIT_MD_ARTIFACT_TOOL].classification == "mutate"
