"""Canonical Markdown artifact submission and completion evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.artifacts as artifacts_package
from ralph.agents import completion_signals as completion_signals_module
from ralph.agents.completion_signals import (
    CompletionSignals,
    _check_completion_sentinel,
    completion_signals_terminal,
    evaluate_completion,
    is_artifact_submitted,
)
from ralph.mcp.artifacts import SubmitResult, submit_artifact_canonical
from ralph.mcp.artifacts import canonical_submit as canonical_submit_module
from ralph.mcp.artifacts.completion_receipts import (
    artifact_receipt_present,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.state_db import MISSING, RunStateDB
from ralph.mcp.tools import coordination as coordination_module
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.phases.required_artifacts import RequiredArtifact
from tests._artifact_format_docs_memory_backend import MemoryBackend
from tests._artifact_format_docs_mock_workspace import MockWorkspace

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
  Verify: pytest tests/test_canonical_artifact_submit.py -q
  Expect: the focused test file reports all tests passed

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


class _RecordingBackend(MemoryBackend):
    """In-memory artifact boundary that exposes directory mutations."""

    def __init__(self) -> None:
        super().__init__()
        self.mkdir_calls: list[Path] = []

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self.mkdir_calls.append(path)
        super().mkdir(path, parents=parents, exist_ok=exist_ok)


def _backend() -> MemoryBackend:
    return MemoryBackend()


@dataclass
class _Session:
    run_id: str
    session_id: str = "test-session"
    drain: str = "development"
    broker_secret: str | None = None
    worker_artifact_dir: Path | None = None
    worker_namespace: Path | None = None

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
) -> SubmitResult:
    return submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type=artifact_type,
        parsed_content=_parsed(artifact_type, markdown),
        markdown=markdown,
        deps=_deps(backend),
        run_id=run_id,
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


def test_canonical_submission_replay_skips_directory_mutations(tmp_path: Path) -> None:
    """S-2: replaying identical canonical output leaves its directories untouched."""
    backend = _RecordingBackend()
    parsed = _parsed("development_result", DEVELOPMENT_RESULT)

    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=DEVELOPMENT_RESULT,
        deps=ArtifactHandlerDeps(backend=backend, history_enabled=False),
    )
    first_directory_mutations = list(backend.mkdir_calls)

    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=DEVELOPMENT_RESULT,
        deps=ArtifactHandlerDeps(backend=backend, history_enabled=False),
    )

    assert backend.mkdir_calls == first_directory_mutations
    artifact_path = tmp_path / ".agent" / "artifacts" / "development_result.md"
    assert backend.read_text(artifact_path) == DEVELOPMENT_RESULT


def test_canonical_submission_identical_replay_does_not_create_history(tmp_path: Path) -> None:
    """S-4: identical replay creates neither an archive nor its history directory."""
    backend = _RecordingBackend()
    parsed = _parsed("development_result", DEVELOPMENT_RESULT)
    deps = ArtifactHandlerDeps(backend=backend, history_enabled=True)

    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=DEVELOPMENT_RESULT,
        deps=deps,
    )
    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=DEVELOPMENT_RESULT,
        deps=deps,
    )

    history_dir = tmp_path / ".agent" / "artifacts" / "history" / "development_result"
    assert not backend.exists(history_dir)


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


def test_submit_artifact_canonical_writes_receipt_without_declaring_completion(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    result = _submit(tmp_path, "commit_message", COMMIT_MESSAGE, backend=backend)

    assert result.receipt_path == tmp_path / ".agent" / "state.db"
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend)
    db = RunStateDB(tmp_path)
    try:
        assert db.get_completion_sentinel_hmac("run-1") is MISSING
    finally:
        db.close()
    assert not _check_completion_sentinel(tmp_path, "run-1")
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


def test_worker_submission_keeps_artifact_and_handoff_inside_worker_namespace(
    tmp_path: Path,
    backend: MemoryBackend,
    workspace: MockWorkspace,
) -> None:
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-api"
    session = _Session(
        run_id="run-worker",
        worker_artifact_dir=worker_namespace / "artifacts",
        worker_namespace=worker_namespace,
    )

    result = handle_submit_md_artifact(
        session,
        workspace,
        {
            "artifact_type": "development_result",
            "content": DEVELOPMENT_RESULT,
        },
        deps=_deps(backend),
    )

    assert result.is_error is False
    assert (
        backend.read_text(worker_namespace / "artifacts" / "development_result.md")
        == DEVELOPMENT_RESULT
    )
    assert (
        backend.read_text(worker_namespace / "handoffs" / "DEVELOPMENT_RESULT.md")
        == DEVELOPMENT_RESULT
    )
    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "development_result.md")
    assert not backend.exists(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md")
    assert artifact_receipt_present(
        tmp_path,
        "run-worker",
        "development_result",
        backend=backend,
    )


def test_two_worker_handoffs_do_not_overwrite_each_other(
    tmp_path: Path,
    backend: MemoryBackend,
    workspace: MockWorkspace,
) -> None:
    first_namespace = tmp_path / ".agent" / "workers" / "unit-api"
    second_namespace = tmp_path / ".agent" / "workers" / "unit-web"
    first_result = DEVELOPMENT_RESULT.replace(
        "Completed the Markdown migration.",
        "Completed unit-api.",
    )
    second_result = DEVELOPMENT_RESULT.replace(
        "Completed the Markdown migration.",
        "Completed unit-web.",
    )

    for run_id, namespace, content in (
        ("run-api", first_namespace, first_result),
        ("run-web", second_namespace, second_result),
    ):
        submitted = handle_submit_md_artifact(
            _Session(
                run_id=run_id,
                worker_artifact_dir=namespace / "artifacts",
                worker_namespace=namespace,
            ),
            workspace,
            {"artifact_type": "development_result", "content": content},
            deps=_deps(backend),
        )
        assert submitted.is_error is False

    assert backend.read_text(first_namespace / "handoffs" / "DEVELOPMENT_RESULT.md") == first_result
    assert (
        backend.read_text(second_namespace / "handoffs" / "DEVELOPMENT_RESULT.md") == second_result
    )
    assert not backend.exists(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md")


def test_repeated_worker_submission_does_not_archive_shared_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend: MemoryBackend,
    workspace: MockWorkspace,
) -> None:
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-api"
    shared_handoff = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
    backend.write_text(shared_handoff, "coordinator-owned handoff")
    session = _Session(
        run_id="run-worker",
        worker_artifact_dir=worker_namespace / "artifacts",
        worker_namespace=worker_namespace,
    )
    updated_result = DEVELOPMENT_RESULT.replace(
        "Completed the Markdown migration.",
        "Completed unit-api after a second pass.",
    )
    deps = ArtifactHandlerDeps(
        backend=backend,
        now_iso=lambda: "2026-07-24T00:00:00+00:00",
    )
    monkeypatch.setattr(
        "ralph.mcp.tools.md_artifact._resolve_history_enabled",
        lambda *_args: True,
    )

    for content in (DEVELOPMENT_RESULT, updated_result):
        submitted = handle_submit_md_artifact(
            session,
            workspace,
            {"artifact_type": "development_result", "content": content},
            deps=deps,
        )
        assert submitted.is_error is False

    assert (
        backend.read_text(worker_namespace / "artifacts" / "development_result.md")
        == updated_result
    )
    assert backend.read_text(shared_handoff) == "coordinator-owned handoff"
    worker_history_dir = worker_namespace / "artifacts" / "history" / "development_result"
    assert not backend.exists(worker_history_dir / "20260724T000000_development_result.md")
    assert not backend.exists(worker_history_dir / "20260724T000000_1_development_result.md")


def test_worker_fallback_promotion_uses_worker_artifact_and_handoff_paths(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-api"
    fallback = worker_namespace / "tmp" / "development_result.md"
    artifact_path = worker_namespace / "artifacts" / "development_result.md"
    backend.write_text(fallback, DEVELOPMENT_RESULT)

    assert is_artifact_submitted(
        tmp_path,
        "run-worker-fallback",
        "development_result",
        deps=deps,
        artifact_path=str(artifact_path),
    )

    assert backend.read_text(artifact_path) == DEVELOPMENT_RESULT
    assert (
        backend.read_text(worker_namespace / "handoffs" / "DEVELOPMENT_RESULT.md")
        == DEVELOPMENT_RESULT
    )
    assert not backend.exists(fallback)
    assert not backend.exists(tmp_path / ".agent" / "artifacts" / "development_result.md")
    assert not backend.exists(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md")


def test_completion_evaluation_passes_required_artifact_path_to_submission_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_path = ".agent/workers/unit-api/artifacts/development_result.md"
    observed: dict[str, object] = {}

    def _submitted(
        workspace_root: Path,
        run_id: str,
        artifact_type: str,
        *,
        deps: ArtifactHandlerDeps | None = None,
        receipt_secret: str | None = None,
        artifact_path: str | None = None,
    ) -> bool:
        del deps, receipt_secret
        observed.update(
            workspace_root=workspace_root,
            run_id=run_id,
            artifact_type=artifact_type,
            artifact_path=artifact_path,
        )
        return True

    monkeypatch.setattr(completion_signals_module, "is_artifact_submitted", _submitted)

    signals = evaluate_completion(
        tmp_path,
        required_artifact=RequiredArtifact(
            phase="development",
            artifact_type="development_result",
            artifact_path=artifact_path,
            markdown_path=None,
            normalizer=None,
        ),
        run_id="run-worker",
    )

    assert signals.required_artifact_present is True
    assert observed["artifact_path"] == artifact_path


def test_submit_artifact_canonical_plan_has_receipt_without_sentinel(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    result = _submit(tmp_path, "plan", PLAN, backend=backend)

    assert result.artifact_path == tmp_path / ".agent" / "artifacts" / "plan.md"
    assert artifact_receipt_present(tmp_path, "run-1", "plan", backend=backend)
    assert result.handoff_path is not None
    assert backend.read_text(result.handoff_path) == PLAN


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


def test_fallback_promotion_regression_commit_message_stamps_receipt_and_removes_tmp_markdown(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    """DA-003: commit artifacts must receive the same fallback promotion as every stage."""
    fallback = tmp_path / ".agent" / "tmp" / "commit_message.md"
    backend.write_text(fallback, COMMIT_MESSAGE)

    result = canonical_submit_module.promote_fallback_artifact(
        tmp_path,
        "commit_message",
        deps=deps,
        run_id="run-1",
    )

    assert result is not None
    assert backend.exists(tmp_path / ".agent" / "artifacts" / "commit_message.md")
    assert not backend.exists(fallback)
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend)
    assert is_artifact_submitted(tmp_path, "run-1", "commit_message", deps=deps)


def test_fallback_promotion_regression_malformed_commit_message_stamps_no_receipt(
    tmp_path: Path,
    backend: MemoryBackend,
    deps: ArtifactHandlerDeps,
) -> None:
    """DA-003: malformed commit fallback documents must not pass commit-stage validation."""
    fallback = tmp_path / ".agent" / "tmp" / "commit_message.md"
    backend.write_text(fallback, "not a markdown artifact")

    assert (
        canonical_submit_module.promote_fallback_artifact(
            tmp_path,
            "commit_message",
            deps=deps,
            run_id="run-2",
        )
        is None
    )
    assert backend.exists(fallback)
    assert not artifact_receipt_present(tmp_path, "run-2", "commit_message", backend=backend)


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


def test_new_run_clears_markdown_fallbacks_without_cleaning_unrelated_json(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    markdown_fallback = tmp_path / ".agent" / "tmp" / "commit_message.md"
    unrelated_state = tmp_path / ".agent" / "tmp" / "worker-state.json"
    backend.write_text(markdown_fallback, COMMIT_MESSAGE)
    backend.write_text(unrelated_state, "opaque internal state")

    canonical_submit_module._clear_fallback_artifacts(
        tmp_path,
        "run-1",
        backend=backend,
    )

    assert not backend.exists(markdown_fallback)
    assert backend.read_text(unrelated_state) == "opaque internal state"


def test_new_worker_run_clears_only_its_namespaced_output_documents(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    worker_tmp = tmp_path / ".agent" / "workers" / "unit-api" / "tmp"
    worker_fallback = worker_tmp / "development_result.md"
    sibling_fallback = (
        tmp_path / ".agent" / "workers" / "unit-web" / "tmp" / "development_result.md"
    )
    shared_fallback = tmp_path / ".agent" / "tmp" / "development_result.md"
    worker_artifact = (
        tmp_path / ".agent" / "workers" / "unit-api" / "artifacts" / "development_result.md"
    )
    worker_handoff = (
        tmp_path / ".agent" / "workers" / "unit-api" / "handoffs" / "DEVELOPMENT_RESULT.md"
    )
    sibling_artifact = (
        tmp_path / ".agent" / "workers" / "unit-web" / "artifacts" / "development_result.md"
    )
    backend.write_text(worker_fallback, DEVELOPMENT_RESULT)
    backend.write_text(sibling_fallback, DEVELOPMENT_RESULT)
    backend.write_text(shared_fallback, DEVELOPMENT_RESULT)
    backend.write_text(worker_artifact, DEVELOPMENT_RESULT)
    backend.write_text(worker_handoff, DEVELOPMENT_RESULT)
    backend.write_text(sibling_artifact, DEVELOPMENT_RESULT)

    canonical_submit_module._clear_worker_artifacts(
        tmp_path,
        "run-worker",
        worker_namespace=worker_tmp.parent,
        backend=backend,
    )

    assert not backend.exists(worker_fallback)
    assert not backend.exists(worker_artifact)
    assert not backend.exists(worker_handoff)
    assert backend.exists(sibling_fallback)
    assert backend.exists(sibling_artifact)
    assert backend.exists(shared_fallback)


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

    assert not coordination_module._write_completion_sentinel(workspace, "run-x")


def test_completion_sentinel_uses_legacy_fallback_when_db_fails(
    tmp_path: Path,
    workspace: MockWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_sqlite_open(*_args: object, **_kwargs: object) -> RunStateDB:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("ralph.mcp.tools.coordination.RunStateDB", _raise_sqlite_open)

    assert coordination_module._write_completion_sentinel(workspace, "run-y")
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
    assert not completion_signals_terminal(signals)


def test_completion_sentinel_is_terminal_without_artifact_contract() -> None:
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
        completion_sentinel_present=True,
    )
    assert completion_signals_terminal(signals)


def test_required_artifact_receipt_without_completion_sentinel_is_not_terminal() -> None:
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=True,
        artifact_types=("development_result",),
        artifact_required=True,
    )
    assert not completion_signals_terminal(signals)


def test_completion_sentinel_without_required_artifact_receipt_is_not_terminal() -> None:
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
        completion_sentinel_present=True,
        artifact_required=True,
    )
    assert not completion_signals_terminal(signals)


def test_required_artifact_receipt_and_completion_sentinel_are_terminal() -> None:
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=True,
        artifact_types=("development_result",),
        completion_sentinel_present=True,
        artifact_required=True,
    )
    assert completion_signals_terminal(signals)
