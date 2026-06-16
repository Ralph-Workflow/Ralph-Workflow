"""Pipeline phases must route artifact submission through the canonical path.

This file verifies that every artifact write during a pipeline-runner phase
goes through the canonical submit machinery (receipt + sentinel) and that
bypasses, fallback promotion, and atomic rollback work correctly.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import is_artifact_submitted
from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.mcp.artifacts.completion_receipts import (
    artifact_receipt_present,
)
from ralph.mcp.tools.artifact import ArtifactHandlerDeps, handle_submit_artifact
from ralph.pipeline.effects.invoke_agent_effect import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.testing.audit_artifact_submission_canonical_path import audit
from ralph.workspace.scope import WorkspaceScope
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

_RUN_ID = "pipeline-test-run"


class _PipelineSession:
    session_id = "sess-pipeline"
    run_id = _RUN_ID
    drain = "execution"

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


def test_pipeline_phase_stamps_canonical_receipt(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A successful pipeline phase leaves a receipt and sentinel via the canonical path."""
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        session = _PipelineSession()
        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "development_result",
                "content": (
                    '{"status": "completed", "summary": "done", "files_changed": "src/main.py"}'
                ),
            },
            deps=ArtifactHandlerDeps(backend=backend),
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.runner.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    runner_mod = importlib.import_module("ralph.pipeline.runner")

    effect = InvokeAgentEffect(
        agent_name="agent1",
        phase="execution",
        prompt_file=str(tmp_path / "PROMPT.md"),
        drain="execution",
    )
    config = UnifiedConfig(general=GeneralConfig())
    workspace_scope = WorkspaceScope(
        root=tmp_path,
        allowed_roots=(tmp_path,),
        local_config_path=tmp_path / ".agent" / "pipeline.toml",
        propagated_config_paths=(),
    )

    result = runner_mod._execute_effect(
        effect,
        config,
        workspace_scope,
        pipeline_deps=object(),
        display_context=object(),
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert artifact_receipt_present(tmp_path, _RUN_ID, "development_result", backend=backend)
    sentinel_path = tmp_path / ".agent" / f"completion_seen_{_RUN_ID}.json"
    assert backend.exists(sentinel_path)


def test_pipeline_audit_finds_no_bypasses_in_isolation() -> None:
    """The pipeline layer has zero canonical-path bypasses when audited in isolation."""
    pipeline_root = Path(__file__).parent.parent / "ralph" / "pipeline"
    findings = audit(codebase_root=pipeline_root)
    assert findings == [], f"Pipeline bypasses found: {findings}"


def test_pipeline_fallback_promotion_uses_canonical_helper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A fallback artifact written without canonical submit is promoted by is_artifact_submitted."""
    del monkeypatch
    backend = MemoryBackend()
    run_id = "pipeline-fallback-test"
    tmp_fallback = tmp_path / ".agent" / "tmp" / "development_result.json"
    payload = '{"status": "completed", "summary": "fallback done", "files_changed": "src/main.py"}'
    backend.mkdir(tmp_fallback.parent, parents=True)
    backend.write_text(tmp_fallback, payload, encoding="utf-8")

    assert not artifact_receipt_present(tmp_path, run_id, "development_result", backend=backend)

    promoted = is_artifact_submitted(
        tmp_path, run_id, "development_result", deps=ArtifactHandlerDeps(backend=backend)
    )

    assert promoted, "is_artifact_submitted should promote fallback and return True"
    assert artifact_receipt_present(tmp_path, run_id, "development_result", backend=backend)


class _FailableBackend(MemoryBackend):
    """MemoryBackend that raises when write_text targets a receipt path."""

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if "receipts" in str(path):
            msg = f"Simulated write failure on receipt path: {path}"
            raise OSError(msg)
        super().write_text(path, content, encoding=encoding)


def test_pipeline_atomic_rollback_on_phase_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """When the receipt write fails, the artifact is rolled back atomically."""
    del monkeypatch
    backend = _FailableBackend()
    workspace = MockWorkspace(tmp_path)

    session = _PipelineSession()

    with pytest.raises(OSError, match="Simulated write failure on receipt path"):
        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "development_result",
                "content": (
                    '{"status": "completed", "summary": "rollback test", '
                    '"files_changed": "src/main.py"}'
                ),
            },
            deps=ArtifactHandlerDeps(backend=backend),
        )

    artifact_file = tmp_path / ".agent" / "artifacts" / "development_result.json"
    assert not backend.exists(artifact_file), (
        "Artifact file should have been rolled back after receipt write failure"
    )
