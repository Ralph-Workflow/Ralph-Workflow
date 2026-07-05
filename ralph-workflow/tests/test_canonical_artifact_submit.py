"""Canonical artifact submission entry point.

``submit_artifact_canonical`` is the single public entry point for producing a
run-scoped completion receipt and the completion sentinel for single-shot types.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.artifacts as artifacts_package
from ralph.agents.completion_signals import (
    CompletionSignals,
    _check_completion_sentinel,
    is_artifact_submitted,
)
from ralph.agents.execution_state._helpers import _check_signals_terminal
from ralph.mcp.artifacts import SubmitResult, submit_artifact_canonical
from ralph.mcp.artifacts import state_db as state_db_module
from ralph.mcp.artifacts.completion_receipts import (
    ReceiptPersistenceError,
    artifact_receipt_present,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.state_db import MISSING, RunStateDB
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_GRANTED: frozenset[str] = frozenset({"artifact.submit"})


def _backend() -> MemoryBackend:
    return MemoryBackend()


def _workspace(tmp_path: Path) -> MockWorkspace:
    return MockWorkspace(tmp_path)


def _deps(backend: MemoryBackend) -> ArtifactHandlerDeps:
    return ArtifactHandlerDeps(backend=backend)


@pytest.fixture
def backend() -> MemoryBackend:
    return _backend()


@pytest.fixture
def workspace(tmp_path: Path) -> MockWorkspace:
    return _workspace(tmp_path)


@pytest.fixture
def deps(backend: MemoryBackend) -> ArtifactHandlerDeps:
    return _deps(backend)


def test_canonical_submit_symbols_exported_from_artifacts_package() -> None:
    assert hasattr(artifacts_package, "SubmitResult")
    assert hasattr(artifacts_package, "submit_artifact_canonical")
    assert hasattr(artifacts_package, "promote_fallback_artifact")


def test_submit_artifact_canonical_exists_and_returns_frozen_result(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    result = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="commit_message",
        parsed_content={"type": "commit", "subject": "feat: test"},
        deps=deps,
        run_id="run-1",
    )

    assert isinstance(result, SubmitResult)
    assert result.artifact_type == "commit_message"
    assert result.run_id == "run-1"
    for field in fields(SubmitResult):
        assert hasattr(result, field.name)


def test_submit_artifact_canonical_writes_artifact_receipt_sentinel_and_handoff(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    result = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="commit_message",
        parsed_content={"type": "commit", "subject": "feat: test"},
        deps=deps,
        run_id="run-1",
    )

    assert result.artifact_path is not None
    assert backend.exists(result.artifact_path)

    # RFC-013 P3: receipt and sentinel are DB-backed (canonical paths are
    # still returned for callers that expect them, but production does
    # NOT write legacy files). Verify via the DB-backed read API.
    assert result.receipt_path is not None
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend)

    assert result.sentinel_path is not None
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-1") is not MISSING
    finally:
        db.close()
    assert _check_completion_sentinel(tmp_path, "run-1") is True

    assert result.handoff_path is None


def test_submit_artifact_canonical_writes_handoff_for_handoff_types(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    result = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content={
            "status": "completed",
            "summary": "done",
            "files_changed": "- x.py",
        },
        deps=deps,
        run_id="run-1",
    )

    assert result.handoff_path is not None
    assert backend.exists(result.handoff_path)


def test_submit_artifact_canonical_does_not_write_sentinel_for_plan(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    result = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="plan",
        parsed_content={
            "summary": {
                "context": "Test plan submission",
                "scope_items": [
                    {"text": "Implement feature"},
                    {"text": "Write tests"},
                    {"text": "Verify"},
                ],
            },
            "skills_mcp": {
                "skills": [
                    "test-driven-development",
                    "verification-before-completion",
                ],
                "mcps": [],
            },
            "steps": [{"number": 1, "title": "Step 1", "content": "Do the work"}],
            "critical_files": {"primary_files": [{"path": "x.py", "action": "modify"}]},
            "risks_mitigations": [{"risk": "Regression", "mitigation": "Tests"}],
            "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
        },
        deps=deps,
        run_id="run-1",
    )

    assert result.artifact_path is not None
    assert backend.exists(result.artifact_path)
    # RFC-013 P3: receipt is DB-backed; legacy file is no longer written.
    assert result.receipt_path is not None
    assert artifact_receipt_present(tmp_path, "run-1", "plan", backend=backend)
    assert result.sentinel_path is None
    assert result.handoff_path is not None


def test_submit_artifact_canonical_rolls_back_on_failure(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RFC-013 P3: receipts are DB-backed, so the canonical submit's
    # receipt op writes through ``RunStateDB.upsert_receipt``. Patch
    # that method to simulate a write failure.
    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("receipt write failed")

    monkeypatch.setattr(
        state_db_module.RunStateDB, "upsert_receipt", _raise, raising=True
    )

    deps = ArtifactHandlerDeps(backend=backend)

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="commit_message",
            parsed_content={"type": "commit", "subject": "feat: test"},
            deps=deps,
            run_id="run-1",
        )

    assert not artifact_receipt_present(
        tmp_path, "run-1", "commit_message", backend=backend
    )
    # Sentinel must also be absent (no DB row was inserted because the
    # earlier receipt op failure triggered the rollback).
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-1") is MISSING
    finally:
        db.close()


def test_submit_artifact_canonical_rolls_back_named_artifact_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("receipt write failed")

    monkeypatch.setattr(
        state_db_module.RunStateDB, "upsert_receipt", _raise, raising=True
    )

    failing_backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=failing_backend)

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="commit_message",
            parsed_content={"type": "commit", "subject": "feat: test"},
            deps=deps,
            run_id="run-named",
            name="custom-name",
        )

    assert not failing_backend.exists(tmp_path / ".agent" / "artifacts" / "custom-name.json")
    assert not failing_backend.exists(tmp_path / ".agent" / "artifacts" / "commit_message.json")
    assert not artifact_receipt_present(
        tmp_path, "run-named", "commit_message", backend=failing_backend
    )


def test_fallback_promotion_stamps_receipt_from_tmp_file(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.json"
    backend.write_text(
        fallback,
        json.dumps(
            {
                "name": "smoke_test_result",
                "type": "smoke_test_result",
                "content": {
                    "status": "passed",
                    "output_file": "tmp/todo-list.js",
                    "observed_working": ["created todo-list.js"],
                    "observed_breaks": [],
                    "headless_guide_checks": ["tool activity"],
                    "summary": "Smoke test passed",
                },
            }
        ),
    )

    assert is_artifact_submitted(tmp_path, "run-1", "smoke_test_result", deps=deps)
    assert artifact_receipt_present(tmp_path, "run-1", "smoke_test_result", backend=backend)


def test_fallback_promotion_handles_bare_payload(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.json"
    backend.write_text(
        fallback,
        json.dumps(
            {
                "status": "passed",
                "output_file": "tmp/todo-list.js",
                "observed_working": ["created todo-list.js"],
                "observed_breaks": [],
                "headless_guide_checks": ["tool activity"],
                "summary": "Smoke test passed",
            }
        ),
    )

    assert is_artifact_submitted(tmp_path, "run-2", "smoke_test_result", deps=deps)
    assert artifact_receipt_present(tmp_path, "run-2", "smoke_test_result", backend=backend)


def test_fallback_promotion_returns_false_on_malformed_json(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.json"
    backend.write_text(fallback, "not valid json")

    assert not is_artifact_submitted(tmp_path, "run-3", "smoke_test_result", deps=deps)


def test_fallback_promotion_prefers_tmp_over_artifacts(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    tmp_fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.json"
    backend.write_text(
        tmp_fallback,
        json.dumps(
            {
                "status": "passed",
                "output_file": "tmp/todo-list.js",
                "observed_working": ["from tmp"],
                "observed_breaks": [],
                "headless_guide_checks": ["tool activity"],
                "summary": "from tmp",
            }
        ),
    )

    artifacts_fallback = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    backend.write_text(
        artifacts_fallback,
        json.dumps(
            {
                "status": "failed",
                "output_file": "tmp/todo-list.js",
                "observed_working": [],
                "observed_breaks": ["from artifacts"],
                "headless_guide_checks": ["tool activity"],
                "summary": "from artifacts",
            }
        ),
    )

    assert is_artifact_submitted(tmp_path, "run-4", "smoke_test_result", deps=deps)
    artifact = backend.read_text(tmp_path / ".agent" / "artifacts" / "smoke_test_result.json")
    assert "from tmp" in artifact


def test_default_backend_is_used_when_deps_is_none(
    tmp_path: Path,
) -> None:
    result = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="commit_message",
        parsed_content={"type": "commit", "subject": "feat: test"},
        run_id="run-1",
    )

    # RFC-013 P3: receipt is DB-backed. The artifact file (always
    # written through the backend) is the canonical artifact target.
    assert result.artifact_path is not None
    assert DEFAULT_FILE_BACKEND.exists(result.artifact_path)
    assert result.receipt_path is not None
    assert artifact_receipt_present(
        tmp_path, "run-1", "commit_message", backend=DEFAULT_FILE_BACKEND
    )


def test_fallback_promotion_returns_false_on_schema_invalid_payload(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    """A schema-invalid fallback payload must not stamp a receipt or artifact."""
    fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.json"
    backend.write_text(
        fallback,
        json.dumps({"bogus": "value"}),
    )

    assert not is_artifact_submitted(tmp_path, "run-x", "smoke_test_result", deps=deps)
    assert not artifact_receipt_present(tmp_path, "run-x", "smoke_test_result", backend=backend)
    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "smoke_test_result.json")


def test_fallback_promotion_continues_after_malformed_tmp_file(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    tmp_fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.json"
    backend.write_text(tmp_fallback, "not valid json")

    artifacts_fallback = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    backend.write_text(
        artifacts_fallback,
        json.dumps(
            {
                "status": "passed",
                "output_file": "tmp/todo-list.js",
                "observed_working": ["from artifacts"],
                "observed_breaks": [],
                "headless_guide_checks": ["tool activity"],
                "summary": "from artifacts",
            }
        ),
    )

    assert is_artifact_submitted(tmp_path, "run-5", "smoke_test_result", deps=deps)
    assert artifact_receipt_present(tmp_path, "run-5", "smoke_test_result", backend=backend)


def test_explicit_completion_marker_alone_is_not_terminal() -> None:

    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=False,
        artifact_types=(),
    )
    assert _check_signals_terminal(signals) is False


def test_explicit_completion_with_sentinel_is_terminal() -> None:

    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=False,
        artifact_types=(),
        completion_sentinel_present=True,
    )
    assert _check_signals_terminal(signals) is True


def test_atomic_rollback_when_artifact_write_fails(
    tmp_path: Path,
) -> None:
    class _FailingBackend(MemoryBackend):
        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            if ".agent/artifacts/" in str(path):
                raise RuntimeError("artifact write failed")
            super().write_text(path, content, encoding=encoding)

    failing_backend = _FailingBackend()
    deps = ArtifactHandlerDeps(backend=failing_backend)

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content={
                "status": "completed",
                "summary": "done",
                "files_changed": "- x.py",
            },
            deps=deps,
            run_id="run-1",
        )

    assert not failing_backend.exists(tmp_path / ".agent" / "artifacts" / "development_result.json")
    assert not failing_backend.exists(
        tmp_path / ".agent" / "receipts" / "run-1" / "development_result.json"
    )
    assert not failing_backend.exists(tmp_path / ".agent" / "completion_seen_run-1.json")


def test_atomic_rollback_when_handoff_sync_fails(
    tmp_path: Path,
) -> None:
    class _FailingBackend(MemoryBackend):
        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            if ".agent/DEVELOPMENT_RESULT.md" in str(path):
                raise RuntimeError("handoff write failed")
            super().write_text(path, content, encoding=encoding)

    failing_backend = _FailingBackend()
    deps = ArtifactHandlerDeps(backend=failing_backend)

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content={
                "status": "completed",
                "summary": "done",
                "files_changed": "- x.py",
            },
            deps=deps,
            run_id="run-1",
        )

    assert not failing_backend.exists(tmp_path / ".agent" / "artifacts" / "development_result.json")
    assert not failing_backend.exists(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md")
    assert not failing_backend.exists(
        tmp_path / ".agent" / "receipts" / "run-1" / "development_result.json"
    )
    assert not failing_backend.exists(tmp_path / ".agent" / "completion_seen_run-1.json")


def test_atomic_rollback_when_receipt_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RFC-013 P3: receipt writes go through RunStateDB.upsert_receipt.
    # Patch that to simulate a write failure and confirm the rest of
    # the submit ops roll back (no artifact file, no sentinel row).
    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("receipt write failed")

    monkeypatch.setattr(
        state_db_module.RunStateDB, "upsert_receipt", _raise, raising=True
    )

    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content={
                "status": "completed",
                "summary": "done",
                "files_changed": "- x.py",
            },
            deps=deps,
            run_id="run-1",
        )

    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "development_result.json")
    assert not backend.exists(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md")
    # No receipt row in DB (upsert raised before commit)
    db = RunStateDB(tmp_path)
    try:
        assert db.get_receipt_hmac("run-1", "development_result") is MISSING
        # Sentinel op must have rolled back too
        assert db.get_completion_sentinel_hmac("run-1") is MISSING
    finally:
        db.close()


def test_atomic_rollback_when_sentinel_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RFC-013 P3: sentinel writes go through RunStateDB.upsert_completion_sentinel.
    # Patch that to simulate a write failure; earlier receipt op must roll back.
    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("sentinel write failed")

    monkeypatch.setattr(
        state_db_module.RunStateDB,
        "upsert_completion_sentinel",
        _raise,
        raising=True,
    )

    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content={
                "status": "completed",
                "summary": "done",
                "files_changed": "- x.py",
            },
            deps=deps,
            run_id="run-1",
        )

    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "development_result.json")
    assert not backend.exists(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md")
    # The earlier receipt op succeeded (it is the sentinel that failed),
    # but the rollback undoes it too. Confirm both DB rows are absent.
    db = RunStateDB(tmp_path)
    try:
        assert db.get_receipt_hmac("run-1", "development_result") is MISSING
        assert db.get_completion_sentinel_hmac("run-1") is MISSING
    finally:
        db.close()


def test_atomic_rollback_preserves_artifact_dir_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_dir = tmp_path / ".agent" / "artifacts"

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("receipt write failed")

    monkeypatch.setattr(
        state_db_module.RunStateDB, "upsert_receipt", _raise, raising=True
    )

    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)

    pre_submit_files = set(backend.glob(artifact_dir, "*.json"))

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content={
                "status": "completed",
                "summary": "done",
                "files_changed": "- x.py",
            },
            deps=deps,
            run_id="run-1",
        )

    post_failure_files = set(backend.glob(artifact_dir, "*.json"))
    assert post_failure_files == pre_submit_files


def test_stale_fallback_not_promoted_for_fresh_run(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    """Test that stale fallback artifacts from previous runs are not promoted to fresh runs.

    Scenario:
    - Previous run (run-old) successfully submitted a development_result artifact
    - Current run (run-new) has no completed artifacts
    - Fallback artifact exists from run-old in .agent/artifacts/
    - Fallback artifact should NOT be promoted to run-new because it's stale

    This ensures artifact isolation between runs and prevents cross-contamination.
    """
    # Create receipt from previous run to indicate it was successfully submitted
    receipt_dir = tmp_path / ".agent" / "receipts" / "run-old"
    backend.write_text(
        receipt_dir / "development_result.json",
        json.dumps(
            {
                "artifact_type": "development_result",
                "run_id": "run-old",
                "timestamp": "2025-01-01T00:00:00Z",
            }
        ),
    )

    # Create stale fallback artifact from previous run
    backend.write_text(
        tmp_path / ".agent" / "artifacts" / "development_result.json",
        json.dumps(
            {
                "status": "completed",
                "summary": "from old run",
                "files_changed": "- x.py",
            }
        ),
    )

    # Current run should not see stale artifact as submitted
    assert not is_artifact_submitted(tmp_path, "run-new", "development_result", deps=deps)
    assert not artifact_receipt_present(tmp_path, "run-new", "development_result", backend=backend)


def test_stale_fallback_not_promoted_when_other_run_has_db_receipt(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    """RFC-013 P3: stale-artifact guard must consult ``RunStateDB`` rows.

    Production writes receipts to ``.agent/state.db`` (RFC-013 P3), so
    the stale-artifact guard inside ``promote_fallback_artifact`` must
    scan DB rows for OTHER run_ids, not only legacy
    ``.agent/receipts/<run>/<type>.json`` files. A canonical artifact
    with only a DB-backed receipt from another run must NOT be promoted
    for the fresh run.
    """
    # Seed a receipt row in the DB for the previous run (no legacy file).
    db = RunStateDB(tmp_path)
    db.upsert_receipt("run-old", "development_result", "sig-old")
    db.close()

    # Create stale fallback artifact from previous run under .agent/artifacts/.
    backend.write_text(
        tmp_path / ".agent" / "artifacts" / "development_result.json",
        json.dumps(
            {
                "status": "completed",
                "summary": "from old run",
                "files_changed": "- x.py",
            }
        ),
    )

    # Current run must NOT see the stale artifact as submitted.
    assert not is_artifact_submitted(tmp_path, "run-new", "development_result", deps=deps)
    assert not artifact_receipt_present(tmp_path, "run-new", "development_result", backend=backend)


# ----------------------------------------------------------------------------
# RFC-013 P3 fail-closed regression: ``ReceiptPersistenceError`` raised
# by ``write_artifact_receipt`` MUST propagate through ``submit_artifact_canonical``
# so the entire submit (artifact, handoff, implicit completion sentinel)
# is unwound atomically. Without this, the agent could falsely claim the
# run is complete against a missing receipt.
# ----------------------------------------------------------------------------


def test_submit_artifact_canonical_rolls_back_when_no_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed integration: when ``write_artifact_receipt`` cannot
    persist the receipt through EITHER the DB OR the legacy-file path,
    ``submit_artifact_canonical`` MUST raise ``ReceiptPersistenceError``
    and roll back every previous op (artifact file, handoff)."""

    def _raise_persistence(
        *_args: object, **_kwargs: object
    ) -> None:
        raise ReceiptPersistenceError(
            "Both DB and legacy paths failed to persist receipt for "
            "run_id='run-1' artifact_type='commit_message'"
        )

    monkeypatch.setattr(
        "ralph.mcp.tools.artifact.write_artifact_receipt",
        _raise_persistence,
    )

    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)

    with pytest.raises(ReceiptPersistenceError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="commit_message",
            parsed_content={"type": "commit", "subject": "feat: test"},
            deps=deps,
            run_id="run-1",
        )

    # Atomic rollback: artifact file is gone.
    assert not backend.exists(
        tmp_path / ".agent" / "artifacts" / "commit_message.json"
    )
    # No receipt row leaked into the DB.
    db = RunStateDB(tmp_path)
    try:
        assert db.get_receipt_hmac("run-1", "commit_message") is MISSING
    finally:
        db.close()


def test_submit_artifact_canonical_succeeds_when_db_upsert_fails_but_legacy_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the dual-read window: when the DB upsert
    raises ``sqlite3.Error`` BUT the legacy-file backend succeeds, the
    submit MUST complete normally. The fail-closed ``ReceiptPersistenceError``
    tightening must NOT regress callers that depend on the legacy
    fallback during the P3 rollout window."""

    def _raise_sqlite(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        state_db_module.RunStateDB, "upsert_receipt", _raise_sqlite, raising=True
    )

    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)

    # Default backend writes the legacy fallback file successfully.
    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="commit_message",
        parsed_content={"type": "commit", "subject": "feat: dual-read"},
        deps=deps,
        run_id="run-1",
    )

    # Legacy file path holds the receipt in the in-memory backend.
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    assert backend.exists(legacy)
