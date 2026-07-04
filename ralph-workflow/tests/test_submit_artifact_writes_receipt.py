"""handle_submit_artifact must stamp a run-scoped receipt on success.

This binds "artifact persisted" and "completion signal emitted" into a single
event so the completion gate can never go blind to a successfully submitted
artifact (the failure that produced "Artifact submitted" + "no artifact"
simultaneously).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.agents.completion_signals import _check_completion_sentinel
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.tools.artifact import ArtifactHandlerDeps, handle_submit_artifact
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_GRANTED: frozenset[str] = frozenset({"artifact.submit"})


@dataclass
class _Session:
    session_id: str = "sess-1"
    run_id: str = "run-1"
    drain: str = "development_commit"
    granted_capabilities: frozenset[str] = field(default_factory=lambda: _GRANTED)

    def check_capability(self, capability: str) -> bool:
        return capability in self.granted_capabilities


def _commit_params() -> dict[str, object]:
    content = json.dumps({"type": "commit", "subject": "feat(board): add drag grip"})
    return {"artifact_type": "commit_message", "content": content}


def test_submit_artifact_writes_receipt(tmp_path: Path) -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    result = handle_submit_artifact(_Session(), workspace, _commit_params(), deps=deps)

    assert result.is_error is False
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend) is True


def test_submit_artifact_receipt_keyed_by_type(tmp_path: Path) -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    handle_submit_artifact(_Session(), workspace, _commit_params(), deps=deps)

    # No receipt should exist for an artifact type that was never submitted.
    assert artifact_receipt_present(tmp_path, "run-1", "plan", backend=backend) is False


def test_submit_artifact_also_writes_completion_sentinel_for_single_shot(
    tmp_path: Path,
) -> None:
    """Single-shot submit must atomically write the completion sentinel.

    Architectural fix (2026-06-14): for single-shot artifact types
    (commit_message, development_result, commit_cleanup, issues, etc.),
    the receipt and the completion sentinel are the SAME event - the
    agent has nothing left to do. Marking completion implicitly prevents
    the production failure mode where a small model interprets the
    submit's success text as "done" and stops without calling
    ``declare_complete`` explicitly, leaving the gate to retry forever
    ("no artifact, no declare_complete" even though the artifact is on
    disk and the receipt is stamped).

    The completion gate's two-signal contract is preserved: receipt
    (artifact persisted) AND sentinel (run finished). The sentinel is
    no longer opt-in for single-shot flows; it is automatic.

    RFC-013 P3: the sentinel is DB-backed. Production does NOT write
    the legacy ``.agent/completion_seen_<run_id>.json`` file path; the
    completion gate reads the DB row first and falls back to the file
    path so an in-flight run surviving an upgrade still passes the
    completion gate. Verify via ``_check_completion_sentinel`` which
    honors both stores.
    """
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    result = handle_submit_artifact(_Session(), workspace, _commit_params(), deps=deps)

    assert result.is_error is False
    assert _check_completion_sentinel(tmp_path, "run-1") is True, (
        "submit_artifact for a single-shot artifact type MUST atomically "
        "register the completion sentinel (DB-backed in RFC-013 P3); "
        "otherwise a small model that interprets the submit success "
        "text as 'done' will leave the run without a completion signal "
        "and the gate will force-retry."
    )


def test_submit_artifact_does_not_write_sentinel_for_planning_decision(
    tmp_path: Path,
) -> None:
    """Multi-step planning artifacts MUST NOT auto-write the sentinel.

    Planning decisions are submitted in the middle of a multi-step
    flow; their completion is the explicit ``finalize_plan`` /
    ``declare_complete`` call. Auto-writing the sentinel here would
    prematurely satisfy the gate mid-flow.
    """
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    planning_params = {
        "artifact_type": "development_analysis_decision",
        "content": json.dumps({"status": "completed", "summary": "x"}),
    }
    result = handle_submit_artifact(_Session(), workspace, planning_params, deps=deps)

    assert result.is_error is False
    sentinel_path = tmp_path / ".agent" / "completion_seen_run-1.json"
    assert not backend.exists(sentinel_path), (
        "submit_artifact for a planning-decision artifact type MUST NOT "
        "auto-write the completion sentinel; completion is the explicit "
        "finalize_plan / declare_complete call."
    )
