"""Public markdown artifact handler contracts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.server._wire_ledger import append_wire_record
from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact, handle_verify_md_artifact
from tests._artifact_format_docs_mock_session import MockSession, planning_session
from tests._artifact_format_docs_mock_workspace import MockWorkspace
from tests._support.typed_accessors import (
    must_dict_list,
)

if TYPE_CHECKING:
    from pathlib import Path


_DOCUMENT = """---
type: commit
subject: test(mcp): cover markdown submission
---
## Body
- [B1] Exercise the public markdown handler.
"""


class _RunSession(MockSession):
    run_id = "run-md-submit"


_BEFORE_HANDLE = "ralph://media/11111111-1111-1111-1111-111111111111"
_AFTER_HANDLE = "ralph://media/22222222-2222-2222-2222-222222222222"


def _design_verdict_document() -> str:
    return f"""---
type: design_verdict
judgement_tier: deterministic
---
## Capture Provenance
run_id: run-md-submit
target: src/ui/header.tsx
before_id: before-capture-set
after_id: after-capture-set
cell_ids: capture-001
verdict_id: verdict-001
before_handles: {_BEFORE_HANDLE}
after_handles: {_AFTER_HANDLE}
## Design Intent
- [I-1] Keep the header controls aligned.
## Verdict
- [V-1] pass | The reviewed capture pair satisfies the declared intent.
## Findings
- [F-1] capture-001 | 0,0,1,1 | alignment | info | No blocking regression.
"""


def _record_active_run_media(workspace: Path, secret: str) -> None:
    for handle in (_BEFORE_HANDLE, _AFTER_HANDLE):
        append_wire_record(
            workspace,
            method="tools/call",
            tool_name="read_media",
            params={"path": handle},
            run_id="run-md-submit",
            secret=secret,
        )


def test_design_verdict_submission_accepts_only_active_run_ledger_captures(tmp_path: Path) -> None:
    """S-4: a verdict may cite only tiered, active-run replay evidence."""
    secret = "artifact-ledger-secret"
    _record_active_run_media(tmp_path, secret)

    result = handle_submit_md_artifact(
        _RunSession(session_id="run-md-submit", broker_secret=secret),
        MockWorkspace(tmp_path),
        {"artifact_type": "design_verdict", "content": _design_verdict_document()},
    )

    assert result.is_error is False


def test_design_verdict_submission_rejects_foreign_or_missing_ledger_captures(tmp_path: Path) -> None:
    """S-4: unminted or another run's handles cannot back a verdict."""
    secret = "artifact-ledger-secret"
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="read_media",
        params={"path": _BEFORE_HANDLE},
        run_id="foreign-run",
        secret=secret,
    )

    result = handle_submit_md_artifact(
        _RunSession(session_id="run-md-submit", broker_secret=secret),
        MockWorkspace(tmp_path),
        {"artifact_type": "design_verdict", "content": _design_verdict_document()},
    )

    assert result.is_error is True
    assert "active run" in result.content[0].text


def test_completed_ui_proof_requires_active_run_ledger_verdict_and_handles(tmp_path: Path) -> None:
    """S-4: completed UI proof requires a submitted active-run verdict and handles."""
    secret = "artifact-ledger-secret"
    session = _RunSession(session_id="run-md-submit", broker_secret=secret)
    workspace = MockWorkspace(tmp_path)
    proof = f"""---
type: development_result
status: completed
---
## Summary
- [SUM-1] Completed the header UI.
## Files Changed
- [F-1] src/ui/header.tsx
## Plan Items Proven
- [UI-4] The header UI is capture-backed.
  Verdict ID: verdict-001
  Before Captures: {_BEFORE_HANDLE}
  After Captures: {_AFTER_HANDLE}
"""

    rejected = handle_submit_md_artifact(
        session, workspace, {"artifact_type": "development_result", "content": proof}
    )
    assert rejected.is_error is True

    _record_active_run_media(tmp_path, secret)
    verdict = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "design_verdict", "content": _design_verdict_document()},
    )
    accepted = handle_submit_md_artifact(
        session, workspace, {"artifact_type": "development_result", "content": proof}
    )

    assert verdict.is_error is False
    assert accepted.is_error is False


def test_submit_writes_canonical_markdown_and_receipt(tmp_path: Path) -> None:
    session = _RunSession(session_id="run-md-submit")
    result = handle_submit_md_artifact(
        session,
        MockWorkspace(tmp_path),
        {"artifact_type": "commit_message", "content": _DOCUMENT},
    )

    assert result.is_error is False
    assert (tmp_path / ".agent" / "artifacts" / "commit_message.md").read_text(
        encoding="utf-8"
    ) == _DOCUMENT
    assert artifact_receipt_present(tmp_path, "run-md-submit", "commit_message")


def test_verify_returns_structured_diagnostics_without_writing(tmp_path: Path) -> None:
    result = handle_verify_md_artifact(
        planning_session(),
        MockWorkspace(tmp_path),
        {"artifact_type": "commit_message", "content": "---\ntype: commit\n---\n"},
    )

    payload = json.loads(result.content[0].text)
    diagnostics = must_dict_list(payload["diagnostics"])
    assert result.is_error is True
    assert diagnostics
    assert {"line", "section", "rule_id", "severity"} <= diagnostics[0].keys()
    assert not (tmp_path / ".agent" / "artifacts" / "commit_message.md").exists()


def test_submit_requires_markdown_content_parameter(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="content"):
        handle_submit_md_artifact(
            planning_session(),
            MockWorkspace(tmp_path),
            {"artifact_type": "commit_message"},
        )
