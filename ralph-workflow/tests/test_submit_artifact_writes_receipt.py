"""Markdown artifact submission stamps a receipt without declaring completion."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.agents.completion_signals import _check_completion_sentinel
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from tests._artifact_format_docs_memory_backend import MemoryBackend
from tests._artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_GRANTED: frozenset[str] = frozenset({"artifact.submit"})


@dataclass
class _Session:
    session_id: str = "sess-1"
    run_id: str = "run-1"
    drain: str = "development_commit"
    granted_capabilities: frozenset[str] = field(default_factory=lambda: _GRANTED)
    broker_secret: str | None = None

    def check_capability(self, capability: str) -> bool:
        return capability in self.granted_capabilities


def _commit_params() -> dict[str, object]:
    content = "---\ntype: commit\nsubject: feat(board): add drag grip\n---\n"
    return {"artifact_type": "commit_message", "content": content}


def test_submit_artifact_writes_receipt(tmp_path: Path) -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    result = handle_submit_md_artifact(_Session(), workspace, _commit_params(), deps=deps)

    assert result.is_error is False
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend) is True


def test_submit_artifact_receipt_keyed_by_type(tmp_path: Path) -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    handle_submit_md_artifact(_Session(), workspace, _commit_params(), deps=deps)

    # No receipt should exist for an artifact type that was never submitted.
    assert artifact_receipt_present(tmp_path, "run-1", "plan", backend=backend) is False


def test_submit_artifact_does_not_implicitly_write_completion_sentinel(
    tmp_path: Path,
) -> None:
    """Submission and explicit phase completion remain separate operations."""
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    result = handle_submit_md_artifact(_Session(), workspace, _commit_params(), deps=deps)

    assert result.is_error is False
    assert _check_completion_sentinel(tmp_path, "run-1") is False


def test_submit_artifact_does_not_write_sentinel_for_planning_decision(
    tmp_path: Path,
) -> None:
    """Analysis submissions also require a separate completion declaration."""
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    planning_params = {
        "artifact_type": "development_analysis_decision",
        "content": (
            "---\n"
            "type: development_analysis_decision\n"
            "status: completed\n"
            "---\n"
            "## Summary\n\n"
            "- [SUM-1] x. Evidence: command output was inspected.\n\n"
            "## Criterion Verdicts\n\n"
            "- [DA-001] Criterion: behavior holds. Expected observation: command output observes it. Verdict: met. Evidence: command output was inspected. Location: source.\n"
        ),
    }
    result = handle_submit_md_artifact(_Session(), workspace, planning_params, deps=deps)

    assert result.is_error is False
    sentinel_path = tmp_path / ".agent" / "completion_seen_run-1.json"
    assert not backend.exists(sentinel_path), (
        "submit_artifact for a planning-decision artifact type MUST NOT "
        "auto-write the completion sentinel; completion is the explicit "
        "finalize_plan / declare_complete call."
    )


def test_submit_artifact_threads_broker_secret_to_receipt_hmac(
    tmp_path: Path,
) -> None:
    """RFC-013 P3: when the session carries a broker_secret, the receipt
    written by ``handle_submit_md_artifact`` is HMAC-bound to that secret.

    This pins the live-wiring contract: a forged receipt (or a receipt
    written without the secret) is rejected by the completion gate when
    the broker configures HMAC enforcement.
    """
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)
    session = _Session()
    session.broker_secret = "live-broker-secret-12345"

    result = handle_submit_md_artifact(session, workspace, _commit_params(), deps=deps)

    assert result.is_error is False
    assert (
        artifact_receipt_present(
            tmp_path,
            "run-1",
            "commit_message",
            backend=backend,
            receipt_secret="live-broker-secret-12345",
        )
        is True
    )
    assert (
        artifact_receipt_present(
            tmp_path,
            "run-1",
            "commit_message",
            backend=backend,
            receipt_secret="wrong-secret",
        )
        is False
    )


def test_submit_artifact_uses_receipt_fallback_when_runstate_db_raises_sqlite_error(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """A DB outage routes the receipt through the durable file fallback.

    If ``RunStateDB`` raises ``sqlite3.Error`` (locked/corrupt/unsupported
    WAL), the artifact submission itself must still succeed without
    propagating the exception. The canonical artifact and legacy receipt are
    persisted together; submission still does not create a completion
    sentinel.
    """
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    def _exploding_init(
        self: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("ralph.mcp.artifacts.state_db.RunStateDB.__init__", _exploding_init)

    result = handle_submit_md_artifact(_Session(), workspace, _commit_params(), deps=deps)

    assert result.is_error is False
    assert backend.exists(tmp_path / ".agent" / "artifacts" / "commit_message.md")
    assert artifact_receipt_present(
        tmp_path,
        "run-1",
        "commit_message",
        backend=backend,
    )
