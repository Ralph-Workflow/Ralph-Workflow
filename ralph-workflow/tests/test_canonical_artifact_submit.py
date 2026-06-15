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
from ralph.agents.completion_signals import is_artifact_submitted
from ralph.mcp.artifacts.canonical_submit import SubmitResult, submit_artifact_canonical
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
            "critical_files": {
                "primary_files": [{"path": "x.py", "action": "modify"}]
            },
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
