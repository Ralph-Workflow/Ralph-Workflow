"""Regression coverage for plans whose retained draft was never submitted."""

from __future__ import annotations

from pathlib import Path

from ralph.agents.completion_signals import completion_signals_terminal, evaluate_completion
from ralph.mcp.artifacts.completion_receipts import write_artifact_receipt
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.mcp.tools.md_artifact import handle_stage_md_artifact, handle_submit_md_artifact
from ralph.phases.required_artifacts import RequiredArtifact
from tests.test_md_artifact_staging import MockSession, _workspace

_PLAN = """---
type: plan
---
## Work

### [S-1] Keep the downstream handoff complete
Inspect the artifact protocol, compare the staged document with canonical storage, and submit this complete plan before completion so the downstream development agent receives every committed verification detail.
"""


def test_plan_submission_regression_unsubmitted_staged_draft_blocks_completion(
    tmp_path: Path,
) -> None:
    """S-1: a draft ahead of canonical content must prevent terminal completion."""
    session = MockSession()
    session.run_id = "plan-run"
    workspace = _workspace(tmp_path)
    handle_submit_md_artifact(session, workspace, {"artifact_type": "plan", "content": _PLAN})
    handle_stage_md_artifact(
        session,
        workspace,
        {"artifact_type": "plan", "content": "\n## Verification\n- Run the complete gate.\n"},
    )
    write_artifact_receipt(tmp_path, session.run_id, "plan")
    state = RunStateDB(tmp_path)
    state.upsert_completion_sentinel(session.run_id, "test-sentinel")
    state.close()
    required = RequiredArtifact(
        phase="planning",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=".agent/PLAN.md",
        normalizer=None,
    )

    signals = evaluate_completion(tmp_path, required_artifact=required, run_id=session.run_id)

    assert not completion_signals_terminal(signals), (
        "the staged plan draft contains content never submitted to the canonical handoff"
    )
