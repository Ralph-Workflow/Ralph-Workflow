"""S-2 regression tests for completion with unresolved artifact validation."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.tools.coordination import handle_declare_complete
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.phases.required_artifacts import retry_hint_path
from ralph.workspace.fs import FsWorkspace
from tests._tool_artifact_2_helper_mocksession import MockSession
from tests._tool_artifact_2_helper_mockworkspace import MockWorkspace

_INVALID_PRODUCT_SPEC = "---\ntype: product_spec\n---\n"
_VALID_PRODUCT_SPEC = """---
type: product_spec
---
## Title
- [T1] Completion validation
## Scope
- [S1] Surface unresolved validation
## Goals
- [G1] Preserve retry context
## Users
- [U1] Agents
## Success Criteria
- [C1] Completion reports validation failure
"""


def _completion_text(result: object) -> str:
    return result.content[0].text


def _declare_complete(session: MockSession, workspace: MockWorkspace) -> object:
    return handle_declare_complete(
        session,
        workspace,
        {"summary": "done"},
        now_fn=lambda: 123,
    )


def test_declare_complete_regression_validation_failure_surfaces_hint_and_persists_sentinel(
    tmp_path: Path,
) -> None:
    """S-2: active validation context blocks a success response, not the sentinel."""
    session = MockSession(drain="development")
    session.run_id = "validation-failure-run"
    workspace = MockWorkspace(tmp_path)
    handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _INVALID_PRODUCT_SPEC},
    )
    hint_path = tmp_path / retry_hint_path("development")
    hint = hint_path.read_text(encoding="utf-8")

    result = _declare_complete(session, workspace)

    assert result.is_error is True
    text = _completion_text(result)
    assert text.startswith("VALIDATION FAILURE")
    assert hint in text
    assert "completion gate" in text.lower()
    assert "re-validate" in text.lower()
    assert (tmp_path / ".agent" / "state.db").exists()


def test_declare_complete_regression_without_validation_hint_keeps_success_contract(
    tmp_path: Path,
) -> None:
    """S-2: no retry hint preserves the existing completion response."""
    session = MockSession(drain="development")
    session.run_id = "normal-completion-run"
    workspace = MockWorkspace(tmp_path)

    result = _declare_complete(session, workspace)

    assert result.is_error is False
    assert _completion_text(result) == (
        "Task declared complete: session_id=test-session, summary='done', timestamp=123\n"
        "[Completion event emitted to pipeline]"
    )


def test_declare_complete_regression_successful_submission_clears_hint_and_restores_success(
    tmp_path: Path,
) -> None:
    """S-2: a valid resubmission clears retry context before completion."""
    session = MockSession(drain="development")
    session.run_id = "recovered-completion-run"
    workspace = MockWorkspace(tmp_path)
    fs_workspace = FsWorkspace(tmp_path)
    handle_submit_md_artifact(
        session,
        fs_workspace,
        {"artifact_type": "product_spec", "content": _INVALID_PRODUCT_SPEC},
    )
    hint_path = tmp_path / retry_hint_path("development")
    assert hint_path.exists()

    submitted = handle_submit_md_artifact(
        session,
        fs_workspace,
        {"artifact_type": "product_spec", "content": _VALID_PRODUCT_SPEC},
    )
    result = _declare_complete(session, workspace)

    assert submitted.is_error is False
    assert hint_path.exists() is False
    assert result.is_error is False
    assert _completion_text(result).startswith("Task declared complete:")
