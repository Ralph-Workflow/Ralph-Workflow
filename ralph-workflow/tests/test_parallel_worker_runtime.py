from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import WORKER_NAMESPACE_ENV
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.prompts.materialize import PromptPhaseContext, PromptPhaseOptions


def _no_agent_registry_class() -> object:
    class _Registry:
        def get(self, name: str) -> None:
            del name

    return type(
        "_FakeRegistryClass",
        (),
        {"from_config": classmethod(lambda cls, config: _Registry())},
    )


def test_worker_runtime_paths_are_namespaced(tmp_path: Path) -> None:
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"

    parallel_module = importlib.import_module("ralph.pipeline.parallel")
    build_paths = getattr(parallel_module, "build_worker_runtime_paths", None)
    if build_paths is None:
        pytest.fail(
            "Expected a supported parallel worker runtime path builder exposed from "
            "ralph.pipeline.parallel with "
            "build_worker_runtime_paths()",
            pytrace=False,
        )

    runtime = build_paths(
        workspace_root=tmp_path,
        worker_namespace=worker_ns,
        phase="development",
    )

    assert runtime.checkpoint_path == worker_ns / "tmp" / "checkpoint.json"
    assert runtime.current_prompt_path == worker_ns / "tmp" / "CURRENT_PROMPT.md"
    assert runtime.prompt_dump_path == worker_ns / "tmp" / "development_prompt.md"
    assert runtime.system_prompt_path == worker_ns / "tmp" / "development_system_prompt.md"
    assert runtime.multimodal_sidecar_path == (
        worker_ns / "tmp" / "development_multimodal_handoff.json"
    )


def test_worker_prompt_helpers_are_namespaced(tmp_path: Path) -> None:
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"

    debug_dump_module = importlib.import_module("ralph.prompts.debug_dump")
    system_prompt_module = importlib.import_module("ralph.prompts.system_prompt")

    worker_prompt_dump_path = getattr(debug_dump_module, "worker_prompt_dump_path", None)
    worker_multimodal_sidecar_path = getattr(
        debug_dump_module, "worker_multimodal_sidecar_path", None
    )
    worker_current_prompt_path = getattr(system_prompt_module, "worker_current_prompt_path", None)
    worker_system_prompt_path = getattr(system_prompt_module, "worker_system_prompt_path", None)

    if (
        worker_prompt_dump_path is None
        or worker_multimodal_sidecar_path is None
        or worker_current_prompt_path is None
        or worker_system_prompt_path is None
    ):
        pytest.fail(
            "Expected worker-specific prompt/system path helpers to be exposed for parallel "
            "worker runtime",
            pytrace=False,
        )

    assert worker_prompt_dump_path(worker_ns, "development") == (
        worker_ns / "tmp" / "development_prompt.md"
    )
    assert worker_multimodal_sidecar_path(worker_ns, "development") == (
        worker_ns / "tmp" / "development_multimodal_handoff.json"
    )
    assert worker_current_prompt_path(worker_ns) == worker_ns / "tmp" / "CURRENT_PROMPT.md"
    assert worker_system_prompt_path(worker_ns, "development") == (
        worker_ns / "tmp" / "development_system_prompt.md"
    )


def test_run_parallel_worker_from_manifest_executes_real_worker_mode_flow(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
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
            captured["workspace_root"] = root
            captured["allowed_roots"] = allowed_roots

        def read(self, path: str) -> str:
            captured["read_path"] = path
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
        del context, options
        captured["materialize_kwargs"] = kwargs
        return shared_prompt

    def _fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        _config: object,
        _pipeline_deps: object,
        _workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        captured["effect"] = effect
        return PipelineEvent.AGENT_SUCCESS

    def _fake_load_config(
        config_path: Path | None,
        cli_overrides: dict[str, object],
        *,
        workspace_scope: object,
    ) -> object:
        captured["config_path"] = config_path
        captured["cli_overrides"] = cli_overrides
        captured["workspace_scope"] = workspace_scope
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
        "build_default_pipeline_deps",
        lambda _config, display_context: make_test_pipeline_deps(
            display_context,
            phase_prompt_materializer=_fake_materialize_prompt_for_phase,
        ),
        raising=False,
    )
    monkeypatch.setattr(module, "FsWorkspace", _FakeWorkspace, raising=False)
    monkeypatch.setattr(
        module,
        "execute_agent_effect",
        _fake_execute_agent_effect,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
        raising=False,
    )
    monkeypatch.setattr(module, "invoke_agent", object(), raising=False)
    monkeypatch.setattr(module, "AgentInvocationError", Exception, raising=False)
    monkeypatch.setattr(
        module,
        "AgentRegistry",
        _no_agent_registry_class(),
        raising=False,
    )

    exit_code = module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=object(),
    )

    assert exit_code == 0
    assert captured["read_path"] == shared_prompt
    assert manifest.config_path is not None
    assert captured["config_path"] == Path(manifest.config_path)
    assert captured["cli_overrides"] == manifest.cli_overrides
    materialize_kwargs = captured["materialize_kwargs"]
    assert isinstance(materialize_kwargs, dict)
    assert materialize_kwargs["worker_namespace"] == worker_ns
    assert materialize_kwargs["work_unit"].unit_id == "unit-a"
    effect = captured["effect"]
    assert isinstance(effect, InvokeAgentEffect)
    assert effect.prompt_file == str(worker_ns / "tmp" / "development_prompt.md")


def test_run_parallel_worker_from_manifest_passes_worker_context_into_execute_agent_effect(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("ralph.pipeline.parallel.worker_runtime")
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
    worker_ns.mkdir(parents=True)
    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        worker_namespace=str(worker_ns),
        worker_artifact_dir=str(worker_ns / "artifacts"),
        prompt_file=str(worker_ns / "tmp" / "development_prompt.md"),
        workspace_root=str(tmp_path),
    )
    manifest_path = tmp_path / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
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

    monkeypatch.setattr(module, "load_config", lambda *args, **kwargs: object(), raising=False)
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
        "build_default_pipeline_deps",
        lambda _config, display_context: make_test_pipeline_deps(
            display_context,
            phase_prompt_materializer=lambda context=None, options=None, **kwargs: (
                ".agent/workers/unit-a/tmp/development_prompt.md"
            ),
        ),
        raising=False,
    )
    monkeypatch.setattr(module, "FsWorkspace", _FakeWorkspace, raising=False)
    monkeypatch.setattr(
        module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
        raising=False,
    )
    monkeypatch.setattr(module, "AgentRegistry", _no_agent_registry_class(), raising=False)

    def _fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        _config: object,
        _pipeline_deps: object,
        _workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        captured["effect"] = effect
        captured["kwargs"] = kwargs
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(module, "execute_agent_effect", _fake_execute_agent_effect, raising=False)

    exit_code = module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=object(),
    )

    assert exit_code == 0
    kwargs = cast("dict[str, object]", captured["kwargs"])
    assert kwargs["worker_namespace"] == worker_ns
    assert kwargs["worker_artifact_dir"] == worker_ns / "artifacts"
    assert kwargs["parallel_worker"] is True


def test_run_parallel_worker_from_manifest_preserves_transport_tool_prefix(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("ralph.pipeline.parallel.worker_runtime")
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
    worker_ns.mkdir(parents=True)
    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        worker_namespace=str(worker_ns),
        worker_artifact_dir=str(worker_ns / "artifacts"),
        prompt_file=str(worker_ns / "tmp" / "development_prompt.md"),
        workspace_root=str(tmp_path),
    )
    manifest_path = tmp_path / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
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

    class _FakeRegistry:
        @classmethod
        def from_config(cls, config: object) -> object:
            del cls, config

            class _Registry:
                def get(self, name: str) -> object:
                    del name
                    return type("_Agent", (), {"transport": AgentTransport.CLAUDE})()

            return _Registry()

    def _fake_materialize_prompt_for_phase(
        context: PromptPhaseContext | None = None,
        options: PromptPhaseOptions | None = None,
        **kwargs: object,
    ) -> str:
        del context, options
        captured.update(kwargs)
        return ".agent/tmp/development_prompt.md"

    monkeypatch.setattr(module, "load_config", lambda *args, **kwargs: object(), raising=False)
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
        "build_default_pipeline_deps",
        lambda _config, display_context: make_test_pipeline_deps(
            display_context,
            phase_prompt_materializer=_fake_materialize_prompt_for_phase,
        ),
    )
    monkeypatch.setattr(module, "FsWorkspace", _FakeWorkspace, raising=False)
    monkeypatch.setattr(
        module,
        "execute_agent_effect",
        lambda _effect, _config, _pipeline_deps, _workspace_scope, **_kwargs: (
            PipelineEvent.AGENT_SUCCESS
        ),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
        raising=False,
    )
    monkeypatch.setattr(module, "AgentRegistry", _FakeRegistry, raising=False)

    exit_code = module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=object(),
    )

    assert exit_code == 0
    session_caps = captured["session_caps"]
    assert hasattr(session_caps, "tool_name_prefix")
    assert cast("str", object.__getattribute__(session_caps, "tool_name_prefix")) == "mcp__ralph__"


def test_run_parallel_worker_from_manifest_does_not_write_worker_checkpoint_without_resume_support(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("ralph.pipeline.parallel.worker_runtime")
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
    worker_ns.mkdir(parents=True)
    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        worker_namespace=str(worker_ns),
        worker_artifact_dir=str(worker_ns / "artifacts"),
        prompt_file=str(worker_ns / "tmp" / "development_prompt.md"),
        workspace_root=str(tmp_path),
    )
    manifest_path = tmp_path / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    save_calls: list[object] = []

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

    monkeypatch.setattr(module, "load_config", lambda *args, **kwargs: object(), raising=False)
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
        "build_default_pipeline_deps",
        lambda _config, display_context: make_test_pipeline_deps(
            display_context,
            phase_prompt_materializer=lambda context=None, options=None, **kwargs: (
                ".agent/tmp/development_prompt.md"
            ),
        ),
        raising=False,
    )
    monkeypatch.setattr(module, "FsWorkspace", _FakeWorkspace, raising=False)
    monkeypatch.setattr(
        module,
        "execute_agent_effect",
        lambda _effect, _config, _pipeline_deps, _workspace_scope, **_kwargs: (
            PipelineEvent.AGENT_SUCCESS
        ),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "AgentRegistry",
        _no_agent_registry_class(),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "save_checkpoint",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
        raising=False,
    )

    exit_code = module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=object(),
    )

    assert exit_code == 0
    assert save_calls == []


def test_materialize_prepared_prompt_passes_worker_work_unit(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt_prep_module = importlib.import_module("ralph.pipeline.prompt_prep")
    captured: dict[str, object] = {}
    unit = WorkUnit(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
    )
    state = PipelineState(phase="development", work_units=(unit,))

    monkeypatch.setattr(
        prompt_prep_module,
        "collect_media_entries_for_phase",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        prompt_prep_module,
        "resolve_phase_drain",
        lambda *args, **kwargs: "development",
        raising=False,
    )

    def _fake_materialize(**kwargs: object) -> str:
        captured.update(kwargs)
        return "ignored"

    prompt_prep_module.materialize_prepared_prompt(
        prompt_prep_module.PreparePromptEffect(phase="development"),
        pipeline_policy=object(),
        artifacts_policy=object(),
        workspace_scope=WorkspaceScope(tmp_path),
        state=state,
        env={str(WORKER_NAMESPACE_ENV): str(tmp_path / ".agent" / "workers" / "unit-a")},
        materialize_fn=_fake_materialize,
    )

    assert captured["work_unit"] == unit


def test_materialize_prepared_prompt_preserves_transport_tool_prefix_from_agent_context(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt_prep_module = importlib.import_module("ralph.pipeline.prompt_prep")
    captured: dict[str, object] = {}
    state = PipelineState(
        phase="development",
        work_units=(
            WorkUnit(
                unit_id="unit-a",
                description="Implement only unit A",
                allowed_directories=["src/a"],
            ),
        ),
    )

    class _RegistryAgent:
        transport = AgentTransport.CLAUDE

    monkeypatch.setattr(
        prompt_prep_module,
        "collect_media_entries_for_phase",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        prompt_prep_module,
        "agents_for_phase",
        lambda *args, **kwargs: ["developer"],
        raising=False,
    )

    def _fake_materialize(**kwargs: object) -> str:
        captured.update(kwargs)
        return "ignored"

    prompt_prep_module.materialize_prepared_prompt(
        prompt_prep_module.PreparePromptEffect(phase="development", drain="development"),
        pipeline_policy=type("_PipelinePolicy", (), {"phases": {}})(),
        artifacts_policy=object(),
        workspace_scope=WorkspaceScope(tmp_path),
        agents_policy=object(),
        registry=type("_Registry", (), {"get": lambda self, name: _RegistryAgent()})(),
        config=object(),
        state=state,
        materialize_fn=_fake_materialize,
    )

    session_caps = captured["session_caps"]
    assert hasattr(session_caps, "tool_name_prefix")
    assert cast("str", object.__getattribute__(session_caps, "tool_name_prefix")) == "mcp__ralph__"
