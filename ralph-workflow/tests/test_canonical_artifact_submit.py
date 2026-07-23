"""Canonical Markdown artifact submission and completion evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
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
from ralph.mcp.artifacts import canonical_submit as canonical_submit_module
from ralph.mcp.artifacts import completion_receipts as completion_receipts_module
from ralph.mcp.artifacts.completion_receipts import (
    ReceiptPersistenceError,
    artifact_receipt_present,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.state_db import MISSING, RunStateDB
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.coordination import _write_completion_sentinel
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path


COMMIT_MESSAGE = """\
---
type: commit
subject: feat: test markdown submission
---

## Body Summary

- [BS-1] Exercise canonical Markdown submission.

## Body Details

- [BD-1] Persist the validated document and completion evidence.

## Files

- [F-1] tests/test_canonical_artifact_submit.py
"""

DEVELOPMENT_RESULT = """\
---
type: development_result
status: completed
---

## Summary

- [SUM-1] Completed the Markdown migration.

## Files Changed

- [F-1] tests/test_canonical_artifact_submit.py

## Plan Items Proven

- [S-1] The focused canonical submission tests pass.

## Analysis Items Addressed

- [FIX-1] Replaced JSON fixtures with validated Markdown.
"""

PLAN = """\
---
type: plan
schema_version: 1
---

## Summary
Test canonical plan submission.

Intent: Preserve plan receipts without a completion sentinel.
Coverage: submission

## Scope
- [SC-1] Submit a valid plan
  Category: test
- [SC-2] Persist its run-scoped receipt
  Category: submission
- [SC-3] Omit the single-shot completion sentinel
  Category: completion

## Skills MCP
Skills: test-driven-development

## Steps

### [S-1] Submit the plan
Submit this validated Markdown document.

Type: file_change
Files:
- modify tests/test_canonical_artifact_submit.py
Satisfies: AC-01

## Critical Files
- [CF-1] tests/test_canonical_artifact_submit.py
  Action: modify
  Changes: migrate canonical submission coverage

## Constraints
Must not break:
- run-scoped receipt behavior

## Design
Exercise the public Markdown submission path.

Outcome: The plan is persisted without a completion sentinel.

## Acceptance Criteria
- [AC-01] A valid plan receives a run-scoped receipt
  Satisfied by: S-1

## Risks
- [R-1] Stale JSON assumptions survive
  Severity: medium
  Mitigation: Assert only canonical Markdown paths.

## Verification
- [V-1] pytest tests/test_canonical_artifact_submit.py -q
  Expect: the focused file passes
"""

SMOKE_TEST_RESULT = """\
---
type: smoke_test_result
status: passed
output_file: tmp/smoke.log
---

## Summary

- [SUM-1] The smoke check passed.

## Observed Working

- [OK-1] Canonical Markdown promotion completed.

## Observed Breaks

- [BR-1] None observed.

## Headless Guide Checks

- [HG-1] completion signal — receipt persisted
"""


def _backend() -> MemoryBackend:
    return MemoryBackend()


@dataclass
class _Session:
    run_id: str
    session_id: str = "test-session"
    drain: str = "development"
    broker_secret: str | None = None

    def check_capability(self, capability: str) -> bool:
        return capability == "artifact.submit"


def _workspace(tmp_path: Path) -> MockWorkspace:
    return MockWorkspace(tmp_path)


def _deps(backend: MemoryBackend) -> ArtifactHandlerDeps:
    return ArtifactHandlerDeps(backend=backend)


def _parsed(artifact_type: str, markdown: str) -> dict[str, object]:
    parsed, diagnostics = parse_and_validate(markdown, get_spec(artifact_type))
    assert not [item for item in diagnostics if item.severity == "error"]
    return dict(parsed)


def _submit(
    tmp_path: Path,
    artifact_type: str,
    markdown: str,
    *,
    backend: MemoryBackend,
    run_id: str = "run-1",
    name: str | None = None,
) -> SubmitResult:
    return submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type=artifact_type,
        parsed_content=_parsed(artifact_type, markdown),
        markdown=markdown,
        deps=_deps(backend),
        run_id=run_id,
        name=name,
    )


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


def test_submit_artifact_canonical_returns_result_and_writes_markdown(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    result = _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    assert isinstance(result, SubmitResult)
    assert result.artifact_type == "commit_message"
    assert result.run_id == "run-1"
    assert result.artifact_path == tmp_path / ".agent" / "artifacts" / "commit_message.md"
    assert backend.read_text(result.artifact_path) == COMMIT_MESSAGE
    for field in fields(SubmitResult):
        assert hasattr(result, field.name)


def test_submit_artifact_canonical_writes_receipt_and_sentinel(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    result = _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    assert result.receipt_path is not None
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend)
    assert result.sentinel_path is not None
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-1") is not MISSING
    finally:
        db.close()
    assert _check_completion_sentinel(tmp_path, "run-1")
    assert result.handoff_path is None


def test_submit_artifact_canonical_writes_byte_identical_handoff(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    result = _submit(
        tmp_path,
        "development_result",
        DEVELOPMENT_RESULT,
        backend=backend,
    )

    assert result.handoff_path is not None
    assert backend.read_text(result.handoff_path) == DEVELOPMENT_RESULT
    assert result.artifact_path is not None
    assert backend.read_text(result.artifact_path) == DEVELOPMENT_RESULT


def test_submit_artifact_canonical_plan_has_receipt_without_sentinel(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    result = _submit(tmp_path, "plan", PLAN, backend=backend)

    assert result.artifact_path == tmp_path / ".agent" / "artifacts" / "plan.md"
    assert artifact_receipt_present(tmp_path, "run-1", "plan", backend=backend)
    assert result.sentinel_path is None
    assert result.handoff_path is not None
    assert backend.read_text(result.handoff_path) == PLAN


def test_named_artifact_uses_markdown_extension(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    result = _submit(
        tmp_path,
        "commit_message",
        COMMIT_MESSAGE,
        backend=backend,
        name="custom-name",
    )

    assert result.artifact_path == tmp_path / ".agent" / "artifacts" / "custom-name.md"
    assert backend.read_text(result.artifact_path) == COMMIT_MESSAGE


def test_fallback_promotion_stamps_receipt_and_removes_tmp_markdown(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"
    backend.write_text(fallback, SMOKE_TEST_RESULT)

    assert is_artifact_submitted(tmp_path, "run-1", "smoke_test_result", deps=deps)
    assert artifact_receipt_present(tmp_path, "run-1", "smoke_test_result", backend=backend)
    canonical = tmp_path / ".agent" / "artifacts" / "smoke_test_result.md"
    assert backend.read_text(canonical) == SMOKE_TEST_RESULT
    assert not backend.exists(fallback)


def test_fallback_promotion_rejects_malformed_markdown(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    fallback = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"
    backend.write_text(fallback, "not a markdown artifact")

    assert not is_artifact_submitted(tmp_path, "run-2", "smoke_test_result", deps=deps)
    assert backend.exists(fallback)
    assert not artifact_receipt_present(tmp_path, "run-2", "smoke_test_result", backend=backend)


def test_default_backend_is_used_when_deps_is_none(tmp_path: Path) -> None:
    result = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="commit_message",
        parsed_content=_parsed("commit_message", COMMIT_MESSAGE),
        markdown=COMMIT_MESSAGE,
        run_id="run-1",
    )

    assert result.artifact_path is not None
    assert DEFAULT_FILE_BACKEND.read_text(result.artifact_path) == COMMIT_MESSAGE
    assert artifact_receipt_present(
        tmp_path,
        "run-1",
        "commit_message",
        backend=DEFAULT_FILE_BACKEND,
    )


def test_invalid_markdown_submission_preserves_last_valid_canonical_state(
    tmp_path: Path,
    backend: MemoryBackend,
    workspace: MockWorkspace,
) -> None:
    _submit(
        tmp_path,
        "development_result",
        DEVELOPMENT_RESULT,
        backend=backend,
        run_id="run-old",
    )
    canonical = tmp_path / ".agent" / "artifacts" / "development_result.md"

    result = handle_submit_md_artifact(
        _Session(run_id="run-new"),
        workspace,
        {"artifact_type": "development_result", "content": "# truncated"},
        deps=_deps(backend),
    )

    assert result.is_error
    assert backend.read_text(canonical) == DEVELOPMENT_RESULT
    assert not artifact_receipt_present(
        tmp_path,
        "run-new",
        "development_result",
        backend=backend,
    )
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-new") is MISSING
    finally:
        db.close()


def test_receipt_failure_propagates_from_current_persistence_module(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_persistence(*_args: object, **_kwargs: object) -> None:
        raise ReceiptPersistenceError("durable receipt unavailable")

    monkeypatch.setattr(
        canonical_submit_module,
        "write_artifact_receipt",
        _raise_persistence,
    )

    with pytest.raises(ReceiptPersistenceError, match="durable receipt unavailable"):
        _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    assert not artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend)
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-1") is MISSING
    finally:
        db.close()


def test_receipt_db_failure_uses_legacy_durable_fallback(
    tmp_path: Path,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_sqlite(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        completion_receipts_module.RunStateDB,
        "upsert_receipt",
        _raise_sqlite,
        raising=True,
    )

    _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    assert backend.exists(legacy)
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend)


def test_completion_sentinel_returns_false_when_db_and_file_writes_fail(
    tmp_path: Path,
    workspace: MockWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_sqlite_open(*_args: object, **_kwargs: object) -> RunStateDB:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("ralph.mcp.tools.coordination.RunStateDB", _raise_sqlite_open)
    monkeypatch.setattr(
        "ralph.mcp.tools.coordination._write_legacy_sentinel_fallback",
        lambda *_args, **_kwargs: False,
    )

    assert not _write_completion_sentinel(workspace, "run-x")


def test_completion_sentinel_uses_legacy_fallback_when_db_fails(
    tmp_path: Path,
    workspace: MockWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_sqlite_open(*_args: object, **_kwargs: object) -> RunStateDB:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("ralph.mcp.tools.coordination.RunStateDB", _raise_sqlite_open)

    assert _write_completion_sentinel(workspace, "run-y")
    assert _check_completion_sentinel(tmp_path, "run-y")


def test_stale_canonical_markdown_is_not_promoted_for_fresh_run(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    _submit(
        tmp_path,
        "development_result",
        DEVELOPMENT_RESULT,
        backend=backend,
        run_id="run-old",
    )

    assert not is_artifact_submitted(tmp_path, "run-new", "development_result", deps=deps)
    assert not artifact_receipt_present(tmp_path, "run-new", "development_result", backend=backend)


def test_explicit_completion_marker_alone_is_not_terminal() -> None:
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=False,
        artifact_types=(),
    )
    assert not _check_signals_terminal(signals)


def test_explicit_completion_with_sentinel_is_terminal() -> None:
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=False,
        artifact_types=(),
        completion_sentinel_present=True,
    )
    assert _check_signals_terminal(signals)
