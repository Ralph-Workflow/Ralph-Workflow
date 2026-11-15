from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import read_artifact_submission_snapshot
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.capability_denied_error import CapabilityDeniedError
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.workspace.memory import MemoryWorkspace

PLAN = """---
type: plan
---

Inspect the existing implementation, preserve current behavior, implement the requested change,
and verify the resulting security boundary with focused automated tests.

## Approved deletions

- `old/a.py`
"""


class Session:
    session_id = "drain-boundary"
    run_id = "current-run"
    broker_secret = "receipt-secret"
    explore_index: object | None = None

    def __init__(self, drain: str, capabilities: set[str]) -> None:
        self.drain = drain
        self.capabilities = capabilities

    def check_capability(self, capability: str) -> object:
        return "approved" if capability in self.capabilities else "denied"


@pytest.mark.parametrize("drain", ["development", "review"])
def test_non_planning_drain_cannot_replace_plan_snapshot(tmp_path: Path, drain: str) -> None:
    submit_artifact_canonical(
        tmp_path,
        "plan",
        {},
        markdown=PLAN,
        run_id="current-run",
        deps=ArtifactHandlerDeps(receipt_secret="receipt-secret"),
    )
    before = read_artifact_submission_snapshot(
        tmp_path, "current-run", "plan", receipt_secret="receipt-secret"
    )
    assert before is not None
    before_path, _before_digest = before
    before_bytes = (tmp_path / before_path).read_bytes()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session = Session(drain, {"artifact.submit", "artifact.plan_read"})

    with pytest.raises(CapabilityDeniedError, match="artifact.plan_write"):
        handle_submit_md_artifact(
            session,
            workspace,
            {
                "artifact_type": "plan",
                "content": PLAN.replace("old/a.py", "old/b.py"),
            },
        )

    after = read_artifact_submission_snapshot(
        tmp_path, "current-run", "plan", receipt_secret="receipt-secret"
    )
    assert after == before
    assert (tmp_path / before_path).read_bytes() == before_bytes


def test_planning_drain_can_submit_plan(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    session = Session(
        "planning",
        {"artifact.submit", "artifact.plan_read", "artifact.plan_write"},
    )

    result = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "plan", "content": PLAN},
    )

    assert result.is_error is False
    assert (
        read_artifact_submission_snapshot(
            tmp_path, "current-run", "plan", receipt_secret="receipt-secret"
        )
        is not None
    )
