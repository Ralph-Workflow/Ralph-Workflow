"""Regression: the parallel worker runtime reports FAILED (no artifact) on
AGENT_FAILURE (S-3, closes PLANNING_ANALYSIS_DECISION.md PA-001).

Split out from ``tests/test_parallel_worker_runtime.py`` (repo-structure's
1000-line file cap) but otherwise mirrors that file's
``test_run_parallel_worker_from_manifest_executes_real_worker_mode_flow``
fixture pattern.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.state import PipelineState
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.pipeline.factory import PhasePromptMaterializerFn, PipelineDeps
    from ralph.prompts.materialize import PromptPhaseContext, PromptPhaseOptions


class _FakePipelineFactory:
    """Conforms to ``PipelineFactory`` and returns a pre-built test bundle."""

    def __init__(
        self,
        phase_prompt_materializer: PhasePromptMaterializerFn | None = None,
    ) -> None:
        self._phase_prompt_materializer = phase_prompt_materializer

    def build(
        self,
        config: object,
        display_context: object,
        **kwargs: object,
    ) -> PipelineDeps:
        del config, kwargs
        return make_test_pipeline_deps(
            display_context,
            phase_prompt_materializer=self._phase_prompt_materializer,
        )


def _no_agent_registry_class() -> object:
    class _Registry:
        def get(self, name: str) -> None:
            del name

    return type(
        "_FakeRegistryClass",
        (),
        {"from_config": classmethod(lambda cls, config: _Registry())},
    )


def test_run_parallel_worker_from_manifest_reports_failed_no_artifact_on_agent_failure(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """F6 / DoD 12 (S-3, closes PLANNING_ANALYSIS_DECISION.md PA-001).

    Mirrors ``test_run_parallel_worker_from_manifest_executes_real_worker_mode_flow``
    (``tests/test_parallel_worker_runtime.py``) but makes the fake
    ``execute_agent_effect`` return ``AGENT_FAILURE`` -- the exact shape the
    completion-enforcement path in ``_completion.py`` produces when a
    required-artifact phase's retry chain exhausts with no receipt and no
    completion sentinel ever written. Proves the REAL
    ``run_parallel_worker_from_manifest`` call site (S-2's restructured
    ``else`` branch), not a helper called in isolation, reaches
    ``render_phase_failure_report`` on that event -- carrying the same
    ``run_id`` that was threaded into ``execute_agent_effect`` -- and that
    ``phase_event_after_agent_run`` (the success-only path) is NOT called.
    """
    module = importlib.import_module("ralph.pipeline.parallel.worker_runtime")
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
    worker_ns.mkdir(parents=True)
    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        config_path=str(tmp_path / "configs" / "worker.toml"),
        cli_overrides={"agent": "opencode", "verbose": True},
        worker_namespace=str(worker_ns),
        worker_artifact_dir=str(worker_ns / "artifacts"),
        prompt_file=str(worker_ns / "tmp" / "development_prompt.md"),
        workspace_root=str(tmp_path),
    )
    manifest_path = tmp_path / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    shared_prompt = ".agent/tmp/development_prompt.md"
    captured: dict[str, object] = {}

    class _FakeWorkspace:
        def __init__(self, root: Path, *, allowed_roots: tuple[Path, ...] | None = None) -> None:
            del root, allowed_roots

        def read(self, path: str) -> str:
            del path
            return "worker prompt body"

    class _PolicyBundle:
        pipeline = object()
        artifacts = object()
        agents = object()

    def _fake_materialize_prompt_for_phase(
        context: PromptPhaseContext | None = None,
        options: PromptPhaseOptions | None = None,
        **kwargs: object,
    ) -> str:
        del context, options, kwargs
        return shared_prompt

    def _fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        _config: object,
        _pipeline_deps: object,
        _workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        del effect
        captured["execute_run_id"] = kwargs.get("run_id")
        return PipelineEvent.AGENT_FAILURE

    def _fake_phase_event_after_agent_run(**kwargs: object) -> PipelineEvent:
        del kwargs
        captured["phase_event_after_agent_run_called"] = True
        return PipelineEvent.AGENT_SUCCESS

    def _fake_render_phase_failure_report(effect: object, **kwargs: object) -> None:
        captured["failure_report_effect"] = effect
        captured["failure_report_run_id"] = kwargs.get("run_id")

    def _fake_load_config(
        config_path: Path | None,
        cli_overrides: dict[str, object],
        *,
        workspace_scope: object,
    ) -> object:
        del config_path, cli_overrides, workspace_scope
        return object()

    monkeypatch.setattr(module, "load_config", _fake_load_config, raising=False)
    monkeypatch.setattr(
        module,
        "load_policy_for_workspace_scope",
        lambda *args, **kwargs: _PolicyBundle(),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "create_initial_state",
        lambda *args, **kwargs: PipelineState(phase="development"),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "determine_effect_from_policy",
        lambda *args, **kwargs: InvokeAgentEffect(
            agent_name="developer",
            phase="development",
            prompt_file="ignored.md",
            drain="development",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: _FakePipelineFactory(
            phase_prompt_materializer=_fake_materialize_prompt_for_phase,
        ),
        raising=False,
    )
    monkeypatch.setattr(module, "FsWorkspace", _FakeWorkspace, raising=False)
    monkeypatch.setattr(module, "execute_agent_effect", _fake_execute_agent_effect, raising=False)
    monkeypatch.setattr(
        module,
        "phase_event_after_agent_run",
        _fake_phase_event_after_agent_run,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "render_phase_failure_report",
        _fake_render_phase_failure_report,
        raising=False,
    )
    monkeypatch.setattr(module, "invoke_agent", object(), raising=False)
    monkeypatch.setattr(module, "AgentInvocationError", Exception, raising=False)
    monkeypatch.setattr(module, "AgentRegistry", _no_agent_registry_class(), raising=False)

    pipeline_deps = _FakePipelineFactory(
        phase_prompt_materializer=_fake_materialize_prompt_for_phase
    ).build(
        object(),
        object(),
    )
    exit_code = module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=object(),
        pipeline_deps=pipeline_deps,
    )

    assert exit_code == 1
    assert "phase_event_after_agent_run_called" not in captured, (
        "phase_event_after_agent_run must NOT be called on AGENT_FAILURE"
    )
    failure_effect = captured["failure_report_effect"]
    assert isinstance(failure_effect, InvokeAgentEffect)
    assert failure_effect.phase == "development"
    assert captured["execute_run_id"] is not None
    assert captured["failure_report_run_id"] == captured["execute_run_id"], (
        "the FAILED-report render must grade the SAME run's evidence the "
        "agent invocation itself was scoped under"
    )
