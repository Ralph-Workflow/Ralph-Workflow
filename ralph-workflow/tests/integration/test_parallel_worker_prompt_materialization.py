"""Parallel worker bootstrap must read shared workspace inputs.

The worker's *agent* is write-restricted to its allowed directories plus its
namespace, but the worker bootstrap itself is trusted orchestrator code: it
must read shared inputs at the repo root (PROMPT.md, plan artifacts) to
materialize the worker prompt, exactly like the serial pipeline does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.parallel import worker_runtime
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from tests.plan_fixtures import MINIMAL_PLAN_MARKDOWN

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_plan_artifact(root: Path) -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.md").write_text(MINIMAL_PLAN_MARKDOWN, encoding="utf-8")


def test_worker_materializes_prompt_from_shared_workspace_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("Implement the modules.", encoding="utf-8")
    (tmp_path / ".agent").mkdir(exist_ok=True)
    (tmp_path / ".agent" / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    _write_plan_artifact(tmp_path)
    worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"
    worker_namespace.mkdir(parents=True, exist_ok=True)
    prompt_file = worker_namespace / "prompt.md"

    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Module A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        config_path=None,
        cli_overrides={},
        worker_namespace=str(worker_namespace),
        worker_artifact_dir=str(worker_namespace / "artifacts"),
        prompt_file=str(prompt_file),
        workspace_root=str(tmp_path),
    )
    manifest_path = worker_namespace / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    executed: list[str] = []

    def _fake_execute_agent_effect(
        effect: object,
        _config: object,
        _pipeline_deps: object,
        _workspace_scope: object,
        **kwargs: object,
    ) -> object:
        executed.append(getattr(effect, "prompt_file", ""))
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(worker_runtime, "execute_agent_effect", _fake_execute_agent_effect)
    monkeypatch.setattr(
        worker_runtime,
        "phase_event_after_agent_run",
        lambda **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    exit_code = worker_runtime.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=make_display_context(),
    )

    assert exit_code == 0
    assert executed == [str(prompt_file)]
    rendered = prompt_file.read_text(encoding="utf-8")
    assert "unit-a" in rendered
    assert "Module A" in rendered
    assert "src/a" in rendered
