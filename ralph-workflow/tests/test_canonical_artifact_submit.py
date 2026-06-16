"""Canonical artifact submission entry point.

``submit_artifact_canonical`` is the single public entry point for producing a
run-scoped completion receipt and the completion sentinel for single-shot types.
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.artifacts as artifacts_package
from ralph.agents.completion_signals import CompletionSignals, is_artifact_submitted
from ralph.agents.execution_state._helpers import _check_signals_terminal
from ralph.mcp.artifacts import SubmitResult, submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
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

    assert result.receipt_path is not None
    assert backend.exists(result.receipt_path)
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend)

    assert result.sentinel_path is not None
    assert backend.exists(result.sentinel_path)
    sentinel_payload = json.loads(backend.read_text(result.sentinel_path, encoding="utf-8"))
    assert sentinel_payload == {"run_id": "run-1"}

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
    assert result.receipt_path is not None
    assert backend.exists(result.receipt_path)
    assert result.sentinel_path is None
    assert result.handoff_path is not None


def test_submit_artifact_canonical_rolls_back_on_failure(
    tmp_path: Path,
    backend: MemoryBackend,
) -> None:
    class _FailingBackend(MemoryBackend):
        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            if ".agent/receipts/" in str(path):
                raise RuntimeError("receipt write failed")
            super().write_text(path, content, encoding=encoding)

    failing_backend = _FailingBackend()
    deps = ArtifactHandlerDeps(backend=failing_backend)

    with pytest.raises(RuntimeError):
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="commit_message",
            parsed_content={"type": "commit", "subject": "feat: test"},
            deps=deps,
            run_id="run-1",
        )

    assert not artifact_receipt_present(
        tmp_path, "run-1", "commit_message", backend=failing_backend
    )
    assert not failing_backend.exists(tmp_path / ".agent" / "completion_seen_run-1.json")


def test_submit_artifact_canonical_rolls_back_named_artifact_on_failure(
    tmp_path: Path,
) -> None:
    class _FailingBackend(MemoryBackend):
        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            if ".agent/receipts/" in str(path):
                raise RuntimeError("receipt write failed")
            super().write_text(path, content, encoding=encoding)

    failing_backend = _FailingBackend()
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

    assert result.receipt_path is not None
    assert DEFAULT_FILE_BACKEND.exists(result.receipt_path)


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
) -> None:
    class _FailingBackend(MemoryBackend):
        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            if ".agent/receipts/" in str(path):
                raise RuntimeError("receipt write failed")
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


def test_atomic_rollback_when_sentinel_write_fails(
    tmp_path: Path,
) -> None:
    class _FailingBackend(MemoryBackend):
        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            if ".agent/completion_seen_" in str(path):
                raise RuntimeError("sentinel write failed")
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


def test_atomic_rollback_preserves_artifact_dir_state(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / ".agent" / "artifacts"

    class _FailingBackend(MemoryBackend):
        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            if ".agent/receipts/" in str(path):
                raise RuntimeError("receipt write failed")
            super().write_text(path, content, encoding=encoding)

    failing_backend = _FailingBackend()
    deps = ArtifactHandlerDeps(backend=failing_backend)

    pre_submit_files = set(failing_backend.glob(artifact_dir, "*.json"))

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

    post_failure_files = set(failing_backend.glob(artifact_dir, "*.json"))
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
