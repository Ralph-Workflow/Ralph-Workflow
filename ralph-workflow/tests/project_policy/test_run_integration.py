from __future__ import annotations

import json
from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.project_policy import analysis as policy_analysis
from ralph.project_policy import markers
from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps

_agent_success_sentinel: PipelineEvent = PipelineEvent.AGENT_SUCCESS

_LoadResult = run_module._LoadResult

_PATCHED_RUN_MODULE_ATTRS: tuple[str, ...] = (
    "_load_configuration",
    "_run_preflight_checks",
    "_sync_shipped_skills_on_pipeline_run",
    "_warn_if_capabilities_degraded",
    "_run_project_policy_readiness",
    "print_dry_run",
    "_execute_pipeline",
    "run_parallel_worker_from_manifest",
    "_maybe_enter_process_view",
)
_ORIGINAL_RUN_MODULE_ATTRS: dict[str, object] = {
    name: getattr(run_module, name) for name in _PATCHED_RUN_MODULE_ATTRS
}


@pytest.fixture(autouse=True)
def _restore_run_module_collaborators() -> None:
    yield
    for name in _PATCHED_RUN_MODULE_ATTRS:
        setattr(run_module, name, _ORIGINAL_RUN_MODULE_ATTRS[name])
    run_module._state.run_func = run_module._RUN_FUNC_UNSET


def _stub_load_result(workspace_root: str, *, phase: str = "planning") -> _LoadResult:
    config = UnifiedConfig()
    workspace_scope = WorkspaceScope(root=workspace_root, allowed_roots=[workspace_root])
    return _LoadResult(
        config=config,
        workspace_scope=workspace_scope,
        initial_state=PipelineState(
            phase=phase,
            policy_entry_phase=phase,
        ),
        policy_bundle=None,
        run_id="test-run-id",
    )


def _ensure_run_func_state_unset() -> None:
    run_module._state.run_func = run_module._RUN_FUNC_UNSET


def _approve_policy(ws: MemoryWorkspace) -> None:
    ws.mkdirs(".agent/artifacts")
    ws.write(
        policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
        json.dumps(
            {
                "type": policy_analysis.ANALYSIS_ARTIFACT_TYPE,
                "content": {"status": "completed", "summary": "policy is sound"},
            }
        ),
    )


@pytest.mark.timeout_seconds(5)
def test_unprepared_project_still_proceeds_to_planning() -> None:
    _ensure_run_func_state_unset()

    runner_invocations: list[dict[str, object]] = []

    def fake_run(config: object, initial_state: object, **kwargs: object) -> int:
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0

    run_module._state.run_func = fake_run

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/project")

    invoke_calls: list[str] = []

    def fake_invoke(*, phase: str, prompt_path: str) -> bool:
        del phase
        invoke_calls.append(prompt_path)
        return True

    emit_messages: list[str] = []

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=emit_messages.append,
        invoke_remediation_agent_factory=lambda _w: fake_invoke,
    )

    assert rc == 0, "policy must NEVER block the development run"
    assert invoke_calls, "remediation agent must still be invoked"
    assert any("remediation-required" in m for m in emit_messages)
    assert any("NOT READY" in m or "could not" in m for m in emit_messages)


@pytest.mark.timeout_seconds(5)
def test_remediation_agent_that_fixes_project_proceeds() -> None:
    _ensure_run_func_state_unset()

    runner_invocations: list[dict[str, object]] = []

    def fake_run(config: object, initial_state: object, **kwargs: object) -> int:
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0

    run_module._state.run_func = fake_run

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/project")

    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    def fake_invoke(*, phase: str, prompt_path: str) -> bool:
        del prompt_path
        if phase == PHASE_REMEDIATION:
            _seed_agents_md(ws)
            _seed_claude_md(ws)
            from ralph.language_detector.models import ProjectStack

            stack = ProjectStack(primary_language="Python")
            _seed_all_core_complete(ws, stack)
            return True
        _approve_policy(ws)
        return True

    emit_messages: list[str] = []

    def fake_emit(message: str) -> None:
        emit_messages.append(message)

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=fake_emit,
        invoke_remediation_agent_factory=lambda _w: fake_invoke,
    )

    assert rc == 0
    assert any("ready" in m for m in emit_messages)


def test_opt_out_skips_readiness_without_writes() -> None:
    _ensure_run_func_state_unset()

    runner_invocations: list[dict[str, object]] = []

    def fake_run(config: object, initial_state: object, **kwargs: object) -> int:
        runner_invocations.append({"config": config, "kwargs": kwargs})
        return 0

    run_module._state.run_func = fake_run

    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        f"# AGENTS.md\n\n{markers.OPT_OUT_MARKER}\nOpted out.\n",
    )
    load_result = _stub_load_result("/test/project")

    emit_messages: list[str] = []

    def fake_emit(message: str) -> None:
        emit_messages.append(message)

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=fake_emit,
    )

    assert rc == 0
    skipped_lines = [m for m in emit_messages if "skipped" in m]
    assert len(skipped_lines) == 1
    assert "opt-out" in skipped_lines[0]
    assert not ws.exists(markers.CACHE_REL_PATH)
    assert not ws.exists(f"{markers.CANONICAL_DIR}testing-policy.md")


def test_readiness_emits_exactly_one_line_for_each_terminal_state() -> None:
    _ensure_run_func_state_unset()

    ws_skipped = MemoryWorkspace()
    ws_skipped.write(
        markers.AGENTS_MD,
        f"# AGENTS.md\n\n{markers.OPT_OUT_MARKER}\n",
    )
    emit_messages_skipped: list[str] = []

    run_module._run_project_policy_readiness(
        load_result=_stub_load_result("/test/skipped"),
        display_context=make_display_context(),
        workspace_factory=lambda: ws_skipped,
        emit_factory=emit_messages_skipped.append,
    )
    assert len(emit_messages_skipped) == 1

    from ralph.language_detector.models import ProjectStack
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    ws_ready = MemoryWorkspace()
    _seed_agents_md(ws_ready)
    _seed_claude_md(ws_ready)
    _seed_all_core_complete(ws_ready, ProjectStack(primary_language="Python"))
    emit_messages_ready: list[str] = []

    rc = run_module._run_project_policy_readiness(
        load_result=_stub_load_result("/test/ready"),
        display_context=make_display_context(),
        workspace_factory=lambda: ws_ready,
        emit_factory=emit_messages_ready.append,
    )
    assert rc == 0
    assert len(emit_messages_ready) == 1
    assert "ready" in emit_messages_ready[0]


def test_remediation_invokes_configured_agent_not_hardcoded_claude() -> None:
    _ensure_run_func_state_unset()

    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.policy.loader import default_dir, load_policy
    from ralph.policy.models._agent_chain_config import AgentChainConfig

    default_bundle = load_policy(default_dir())
    custom_chain = AgentChainConfig(agents=["custom-agent"], max_retries=2, retry_delay_ms=1000)
    new_chains = dict(default_bundle.agents.agent_chains)
    new_chains["policy_remediation"] = custom_chain
    new_agents = default_bundle.agents.model_copy(update={"agent_chains": new_chains})
    fake_bundle = default_bundle.model_copy(update={"agents": new_agents})

    ws = MemoryWorkspace()

    observed_effects: list[object] = []

    def fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *,
        bridge: object = None,
        raw_output_sink: object = None,
        rendered_output_sink: object = None,
        run_id: str | None = None,
        required_artifact: object = None,
        session_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        raise_resumable_exit: bool = False,
        agent_invocation_error_sink: Callable[[Exception], object] | None = None,
        **opts: object,
    ) -> PipelineEvent:
        observed_effects.append(effect)
        from ralph.language_detector.models import ProjectStack
        from tests.project_policy.test_validator import (
            _seed_agents_md,
            _seed_all_core_complete,
            _seed_claude_md,
        )

        _seed_agents_md(ws)
        _seed_claude_md(ws)
        _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))
        return _agent_success_sentinel

    original_executor_module = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = fake_execute_agent_effect

    load_result = _LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=fake_bundle,
        run_id="test-run-id",
    )

    try:
        rc = run_module._run_project_policy_readiness(
            load_result=load_result,
            display_context=make_display_context(),
            workspace_factory=lambda: ws,
            emit_factory=lambda m: None,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor_module

    assert rc == 0
    assert observed_effects, "execute_agent_effect must be invoked"
    first_effect: object = observed_effects[0]
    agent_name_attr: object = getattr(first_effect, "agent_name", None)
    assert agent_name_attr == "custom-agent", (
        f"agent_name was {agent_name_attr!r}; expected 'custom-agent' from the configured chain"
    )


def test_helper_does_not_emit_blocked_panel_when_agent_uses_run_id() -> None:
    _ensure_run_func_state_unset()

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/sanity")

    from ralph.language_detector.models import ProjectStack
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=lambda m: None,
    )

    assert rc == 0
    assert ws.exists(markers.AGENTS_MD)
    assert ws.exists(markers.CLAUDE_MD)


class _RecordingRunners:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reset(self) -> None:
        self.calls = []


def _patch_run_pipeline_collaborators(
    monkeypatch_target: ModuleType,
    *,
    load_result: _LoadResult,
    preflight_rc: int,
    policy_readiness_rc: int,
    runners: _RecordingRunners,
) -> dict[str, object]:
    stubs: dict[str, object] = {}

    def fake_load_configuration(*args: object, **kwargs: object) -> _LoadResult:
        runners.calls.append("load_configuration")
        return load_result

    def fake_run_preflight_checks(*args: object, **kwargs: object) -> int:
        runners.calls.append("run_preflight_checks")
        return preflight_rc

    def fake_sync_shipped_skills(*args: object, **kwargs: object) -> None:
        runners.calls.append("sync_shipped_skills")

    def fake_warn_capabilities(*args: object, **kwargs: object) -> None:
        runners.calls.append("warn_capabilities")

    def fake_run_project_policy_readiness(*args: object, **kwargs: object) -> int:
        runners.calls.append("run_project_policy_readiness")
        return policy_readiness_rc

    def fake_print_dry_run(*args: object, **kwargs: object) -> None:
        runners.calls.append("print_dry_run")

    def fake_execute_pipeline(*args: object, **kwargs: object) -> int:
        runners.calls.append("execute_pipeline")
        return 0

    def fake_run_parallel_worker(*args: object, **kwargs: object) -> int:
        runners.calls.append("run_parallel_worker_from_manifest")
        return 0

    monkeypatch_target._load_configuration = fake_load_configuration
    monkeypatch_target._run_preflight_checks = fake_run_preflight_checks
    monkeypatch_target._sync_shipped_skills_on_pipeline_run = fake_sync_shipped_skills
    monkeypatch_target._warn_if_capabilities_degraded = fake_warn_capabilities
    monkeypatch_target._run_project_policy_readiness = fake_run_project_policy_readiness
    monkeypatch_target.print_dry_run = fake_print_dry_run
    monkeypatch_target._execute_pipeline = fake_execute_pipeline
    monkeypatch_target.run_parallel_worker_from_manifest = fake_run_parallel_worker
    monkeypatch_target._maybe_enter_process_view = lambda _stack: None

    stubs["load_configuration"] = fake_load_configuration
    stubs["run_preflight_checks"] = fake_run_preflight_checks
    stubs["sync_shipped_skills"] = fake_sync_shipped_skills
    stubs["warn_capabilities"] = fake_warn_capabilities
    stubs["run_project_policy_readiness"] = fake_run_project_policy_readiness
    stubs["print_dry_run"] = fake_print_dry_run
    stubs["execute_pipeline"] = fake_execute_pipeline
    stubs["run_parallel_worker"] = fake_run_parallel_worker
    return stubs


def test_run_pipeline_invokes_readiness_before_execution() -> None:
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/pipeline-order")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=0,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=False,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        parallel_worker_manifest=None,
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    assert rc == 0
    expected = [
        "load_configuration",
        "run_preflight_checks",
        "sync_shipped_skills",
        "warn_capabilities",
        "run_project_policy_readiness",
        "execute_pipeline",
    ]
    assert runners.calls == expected


def test_run_pipeline_nonzero_readiness_still_reaches_dry_run_and_execute() -> None:
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/pipeline-blocked")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=2,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=True,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        parallel_worker_manifest=None,
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    assert rc != 2, "a policy failure must not become the run's exit code"
    assert runners.calls[:5] == [
        "load_configuration",
        "run_preflight_checks",
        "sync_shipped_skills",
        "warn_capabilities",
        "run_project_policy_readiness",
    ]
    assert "print_dry_run" in runners.calls, (
        "an unready policy must not prevent the run from proceeding"
    )


def test_run_pipeline_dry_run_invokes_printer_after_ready() -> None:
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/dry-run")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=0,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=True,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        parallel_worker_manifest=None,
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    assert rc == 0
    assert runners.calls == [
        "load_configuration",
        "run_preflight_checks",
        "sync_shipped_skills",
        "warn_capabilities",
        "run_project_policy_readiness",
        "print_dry_run",
    ]
    assert "execute_pipeline" not in runners.calls


def test_run_pipeline_parallel_worker_manifest_short_circuits_before_readiness() -> None:
    _ensure_run_func_state_unset()
    runners = _RecordingRunners()
    load_result = _stub_load_result("/test/parallel-worker")
    _patch_run_pipeline_collaborators(
        run_module,
        load_result=load_result,
        preflight_rc=0,
        policy_readiness_rc=0,
        runners=runners,
    )

    request = run_module.RunPipelineRequest(
        config_path=None,
        cli_overrides=None,
        dry_run=False,
        resume=False,
        verbosity=None,
        counter_overrides=None,
        parallel_worker_manifest="unused-manifest-path",
        pro_hooks=None,
        model_identity=None,
    )
    rc = run_module.run_pipeline(
        request=request,
        display_context=make_display_context(),
    )

    assert rc == 0
    assert runners.calls == ["run_parallel_worker_from_manifest"]
    assert "run_project_policy_readiness" not in runners.calls
    assert "load_configuration" not in runners.calls
