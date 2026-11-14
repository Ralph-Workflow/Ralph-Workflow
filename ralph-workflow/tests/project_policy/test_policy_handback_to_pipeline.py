from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.language_detector.models import ProjectStack
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline._runner_session import (
    apply_session_capture,
    pop_last_captured_session_id,
    set_last_captured_session_id,
)
from ralph.pipeline.agent_retry_intent import (
    AgentRetryIntent,
    cleared_agent_retry_intent,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.project_policy import analysis as policy_analysis
from ralph.project_policy import cli_integration
from ralph.project_policy.policy_mode import PolicyMode
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests.project_policy.policy_corpus import (
    policy_invocation_bundle,
    remediation_required_result,
)
from tests.project_policy.test_validator import (
    _seed_agents_md,
    _seed_all_core_complete,
    _seed_claude_md,
)

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps
    from ralph.workspace.protocol import Workspace

_agent_success_sentinel: PipelineEvent = PipelineEvent.AGENT_SUCCESS

LEAKED_POLICY_SESSION_ID: str = "policy-remediation-leaked-session"


def _seed_workspace_with_complete_policy(ws: MemoryWorkspace) -> None:
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))


def _approve_policy_in_workspace(ws: MemoryWorkspace) -> None:
    ws.mkdirs(".agent/artifacts")
    ws.write(
        policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
        (
            "---\n"
            f"type: {policy_analysis.ANALYSIS_ARTIFACT_TYPE}\n"
            "status: completed\n"
            "---\n\n"
            "## Summary\n\n"
            "- [SUM-1] Policy is sound; every committed criterion is met.\n"
            "\n## Criterion Verdicts\n\n"
            "- [PR-001] Criterion: the declared verification command resolves. "
            "Expected observation: `make verify` invokes a target. "
            "Verdict: met. Evidence: make reports the rule. "
            "Location: verification-policy.md RALPH-COMMAND.\n"
        ),
    )


def _build_load_result(
    *,
    workspace_root: str,
    policy_bundle: object | None = None,
) -> run_module._LoadResult:
    return run_module._LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root=workspace_root,
            allowed_roots=[workspace_root],
        ),
        initial_state=PipelineState(
            phase="planning",
            policy_entry_phase="planning",
        ),
        policy_bundle=policy_bundle,
        run_id="test-run-id",
    )


def test_policy_phase_does_not_leak_session_id_into_pipeline_consumer() -> None:
    bundle = policy_invocation_bundle()
    policy_workspace = MemoryWorkspace()

    def fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> PipelineEvent:
        del config, pipeline_deps, workspace_scope, opts
        set_last_captured_session_id(LEAKED_POLICY_SESSION_ID)
        effect_executor_module._set_last_captured_retry_intent(cleared_agent_retry_intent())
        if effect.phase == "policy_remediation":
            _seed_workspace_with_complete_policy(policy_workspace)
        elif effect.phase == "policy_remediation_analysis":
            _approve_policy_in_workspace(policy_workspace)
        return PipelineEvent.AGENT_SUCCESS

    original = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = fake_execute_agent_effect
    try:
        load_result = _build_load_result(
            workspace_root="/test/project-policy-handback",
            policy_bundle=bundle,
        )

        rc = run_module._run_project_policy_readiness(
            load_result=load_result,
            display_context=make_display_context(),
            workspace_factory=lambda: policy_workspace,
            emit_factory=lambda m: None,
        )
    finally:
        effect_executor_module.execute_agent_effect = original

    assert rc == 0, "policy preflight should exit 0 once remediation is ready"

    leaked_session_id = pop_last_captured_session_id()
    leaked_intent = effect_executor_module.pop_last_captured_retry_intent()

    assert leaked_session_id is None, (
        f"policy phase leaked session id {leaked_session_id!r}; "
        "the pipeline runner would resume this conversation in Phase 4. "
        "Snapshot/restore the session-capture thread-locals around each "
        "policy agent invocation in cli_integration._make_production_invoke_agent."
    )
    assert leaked_intent == cleared_agent_retry_intent(), (
        f"policy phase leaked retry intent {leaked_intent!r}; "
        "the pipeline runner would apply it to its own state in Phase 4."
    )

    fresh_state = PipelineState(phase="planning", policy_entry_phase="planning")
    drained_state = apply_session_capture(fresh_state)
    assert drained_state.last_agent_session_id is None, (
        f"apply_session_capture picked up a leaked session id: "
        f"{drained_state.last_agent_session_id!r}"
    )
    assert drained_state.agent_retry_intent == cleared_agent_retry_intent(), (
        f"apply_session_capture picked up a leaked retry intent: "
        f"{drained_state.agent_retry_intent!r}"
    )


def test_policy_phase_failure_path_does_not_leak_retry_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_integration, "SystemClock", FakeClock)
    bundle = policy_invocation_bundle()
    leaked_intent_action = "fresh"
    leaked_intent_reason = "policy-remediation-leaked-failure"
    policy_workspace = MemoryWorkspace()

    def fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> PipelineEvent:
        del effect, config, pipeline_deps, workspace_scope, opts
        effect_executor_module._set_last_captured_retry_intent(
            AgentRetryIntent(
                action=leaked_intent_action,
                failure_reason=leaked_intent_reason,
            )
        )
        set_last_captured_session_id(None)
        return PipelineEvent.AGENT_FAILURE

    original = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = fake_execute_agent_effect
    try:
        load_result = _build_load_result(
            workspace_root="/test/project-policy-handback-failure",
            policy_bundle=bundle,
        )

        cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=make_display_context(),
            workspace_factory=lambda: policy_workspace,
            emit_factory=lambda m: None,
            is_tty=lambda: False,
        )
    finally:
        effect_executor_module.execute_agent_effect = original

    leaked_intent = effect_executor_module.pop_last_captured_retry_intent()
    assert leaked_intent == cleared_agent_retry_intent(), (
        f"failure-path policy phase leaked retry intent {leaked_intent!r}; "
        "the pipeline runner would apply this intent to its first attempt."
    )


LEAKED_POLICY_SESSION_ID: str = "policy-remediation-session"

_PERSISTED_NON_TERMINAL_PHASES: tuple[str, ...] = (
    "planning",
    "planning_analysis",
    "development",
    "development_analysis",
    "development_commit",
)


def _build_policy_phase_leak_fake(
    effect_executor_module: ModuleType,
    ws: MemoryWorkspace,
    policy_phase_session_ids: list[str | None],
) -> Callable[..., PipelineEvent]:
    from ralph.language_detector.models import ProjectStack
    from ralph.pipeline._runner_session import set_last_captured_session_id
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    def fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> PipelineEvent:
        del config, pipeline_deps, workspace_scope, opts
        set_last_captured_session_id(LEAKED_POLICY_SESSION_ID)
        effect_executor_module._set_last_captured_retry_intent(cleared_agent_retry_intent())
        policy_phase_session_ids.append(LEAKED_POLICY_SESSION_ID)
        if effect.phase == "policy_remediation":
            _seed_agents_md(ws)
            _seed_claude_md(ws)
            _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))
        elif effect.phase == "policy_remediation_analysis":
            _approve_policy_in_workspace(ws)
        return _agent_success_sentinel

    return fake_execute_agent_effect


def _build_handback_pipeline_stubs(
    *,
    load_result: run_module._LoadResult,
    ws: MemoryWorkspace,
    monkeypatch: pytest.MonkeyPatch,
    preflight_order: list[str],
    captured_pipeline_state: list[PipelineState],
    captured_first_effects: list[object] | None = None,
    working_tree_snapshots: list[str] | None = None,
    policy_commit_requests: list[frozenset[str] | None] | None = None,
) -> dict[str, object]:
    from ralph.pipeline._runner_session import apply_session_capture

    def stub_load_configuration(*args: object, **kwargs: object) -> run_module._LoadResult:
        del args, kwargs
        preflight_order.append("load_configuration")
        return load_result

    def stub_preflight_checks(*args: object, **kwargs: object) -> int:
        del args, kwargs
        preflight_order.append("run_preflight_checks")
        return 0

    def stub_sync_shipped_skills(*args: object, **kwargs: object) -> None:
        del args, kwargs
        preflight_order.append("sync_shipped_skills")

    def stub_warn_capabilities(*args: object, **kwargs: object) -> None:
        del args, kwargs
        preflight_order.append("warn_capabilities")

    def stub_policy_readiness(
        *,
        load_result: run_module._LoadResult,
        display_context: DisplayContext,
        display: object | None = None,
        mode: PolicyMode = PolicyMode.NORMAL,
        workspace_factory: Callable[[], Workspace] | None = None,
        emit_factory: Callable[[str], None] | None = None,
        invoke_remediation_agent_factory: Callable[[Workspace], object] | None = None,
    ) -> int:
        del mode, invoke_remediation_agent_factory
        preflight_order.append("run_project_policy_readiness")

        def snapshot_working_tree(scope: WorkspaceScope) -> frozenset[str]:
            if working_tree_snapshots is not None:
                working_tree_snapshots.append(scope.root)
            return frozenset()

        def commit_policy_changes(
            _scope: WorkspaceScope,
            _pre_run_dirty: frozenset[str] | None,
        ) -> None:
            if policy_commit_requests is not None:
                policy_commit_requests.append(frozenset())

        with monkeypatch.context() as policy_monkeypatch:
            policy_monkeypatch.setattr(
                cli_integration,
                "run_policy_readiness_preflight",
                lambda *_args, **_kwargs: remediation_required_result(),
            )
            return cli_integration.run_project_policy_readiness(
                load_result=load_result,
                display_context=display_context,
                display=display,
                workspace_factory=lambda: ws,
                emit_factory=emit_factory,
                is_tty=lambda: False,
                working_tree_snapshot=snapshot_working_tree,
                commit_policy_updates=commit_policy_changes,
            )

    def stub_execute_pipeline(
        *args: object,
        **kwargs: object,
    ) -> int:
        candidate: object = args[0] if args else kwargs.get("request")
        initial_state: PipelineState | None = getattr(candidate, "initial_state", None)
        if initial_state is None:
            raise AssertionError("stub_execute_pipeline received no initial_state")
        preflight_order.append("execute_pipeline")
        drained_state = apply_session_capture(initial_state)
        captured_pipeline_state.append(drained_state)
        from ralph.pipeline.orchestrator import determine_next_effect

        if load_result.policy_bundle is not None:
            effect = determine_next_effect(
                drained_state,
                load_result.policy_bundle.pipeline,
                load_result.policy_bundle.agents,
            )
            captured_first_effects.append(effect)
        return 0

    return {
        "_load_configuration": stub_load_configuration,
        "_run_preflight_checks": stub_preflight_checks,
        "_sync_shipped_skills_on_pipeline_run": stub_sync_shipped_skills,
        "_warn_if_capabilities_degraded": stub_warn_capabilities,
        "_run_project_policy_readiness": stub_policy_readiness,
        "_execute_pipeline": stub_execute_pipeline,
    }


def _assert_handback_state_clean(
    *,
    phase: str,
    preflight_order: list[str],
    policy_phase_session_ids: list[str | None],
    captured_pipeline_state: list[PipelineState],
    captured_first_effects: list[object] | None = None,
) -> None:
    from ralph.pipeline.effects import PreparePromptEffect

    assert "run_project_policy_readiness" in preflight_order, (
        f"policy preflight did not run: {preflight_order!r}"
    )
    assert "execute_pipeline" in preflight_order, (
        f"phase 4 was not reached after the policy preflight: {preflight_order!r}"
    )
    assert preflight_order.index("run_project_policy_readiness") < preflight_order.index(
        "execute_pipeline"
    ), f"phase 4 must follow the policy preflight: {preflight_order!r}"
    assert policy_phase_session_ids, (
        "production policy closure did not invoke execute_agent_effect; "
        "test cannot observe the leak"
    )
    assert len(captured_pipeline_state) == 1, (
        f"_execute_pipeline was reached {len(captured_pipeline_state)} times; "
        "expected exactly one observation of the state at the Phase 4 seam"
    )
    observed_state = captured_pipeline_state[0]
    assert observed_state.last_agent_session_id is None, (
        f"phase={phase!r}: apply_session_capture picked up a leaked session "
        f"id {observed_state.last_agent_session_id!r}; the pipeline runner "
        "would resume the remediation conversation in Phase 4"
    )
    assert observed_state.agent_retry_intent == cleared_agent_retry_intent(), (
        f"phase={phase!r}: apply_session_capture picked up a leaked retry "
        f"intent {observed_state.agent_retry_intent!r}; the pipeline runner "
        "would apply it to its own first attempt"
    )
    assert observed_state.phase == phase, (
        f"phase={phase!r}: observed phase is {observed_state.phase!r}; "
        "the run did not return to the persisted phase"
    )
    assert observed_state.policy_entry_phase == phase, (
        f"phase={phase!r}: observed policy_entry_phase is "
        f"{observed_state.policy_entry_phase!r}; the run did not preserve the "
        "persisted entry phase"
    )
    if captured_first_effects is not None:
        assert captured_first_effects, (
            f"phase={phase!r}: determine_next_effect was never invoked; "
            "the S-3 contract cannot be observed"
        )
        first_effect = captured_first_effects[0]
        assert isinstance(first_effect, PreparePromptEffect), (
            f"phase={phase!r}: first effect is {type(first_effect).__name__}, "
            f"expected PreparePromptEffect; the run did not resume the "
            f"pre-policy phase"
        )
        assert first_effect.phase == phase, (
            f"phase={phase!r}: first effect's phase is {first_effect.phase!r}, "
            f"expected {phase!r}; the run did not resume the pre-policy phase"
        )
        assert first_effect.drain == phase, (
            f"phase={phase!r}: first effect's drain is {first_effect.drain!r}, "
            f"expected {phase!r}; the run did not route to the pre-policy drain"
        )


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize(
    "phase", _PERSISTED_NON_TERMINAL_PHASES, ids=_PERSISTED_NON_TERMINAL_PHASES
)
def test_run_pipeline_handback_returns_to_persisted_phase_not_policy_session(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_run_func_state_unset()

    from ralph.pipeline import effect_executor as effect_executor_module

    real_bundle = policy_invocation_bundle()
    ws = MemoryWorkspace()

    policy_phase_session_ids: list[str | None] = []
    fake_execute_agent_effect = _build_policy_phase_leak_fake(
        effect_executor_module, ws, policy_phase_session_ids
    )

    original_executor_module = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = fake_execute_agent_effect

    load_result = _stub_load_result_for_handback(f"/test/handback-{phase}", phase=phase)
    load_result = run_module._LoadResult(
        config=load_result.config,
        workspace_scope=load_result.workspace_scope,
        initial_state=load_result.initial_state,
        policy_bundle=real_bundle,
        run_id=load_result.run_id,
    )

    captured_pipeline_state: list[PipelineState] = []
    preflight_order: list[str] = []
    captured_first_effects: list[object] = []
    working_tree_snapshots: list[str] = []
    policy_commit_requests: list[frozenset[str] | None] = []

    stubs = _build_handback_pipeline_stubs(
        load_result=load_result,
        ws=ws,
        monkeypatch=monkeypatch,
        preflight_order=preflight_order,
        captured_pipeline_state=captured_pipeline_state,
        captured_first_effects=captured_first_effects,
        working_tree_snapshots=working_tree_snapshots,
        policy_commit_requests=policy_commit_requests,
    )

    original_readiness = run_module._run_project_policy_readiness
    original_execute = run_module._execute_pipeline
    original_load = run_module._load_configuration
    original_preflight = run_module._run_preflight_checks
    original_sync = run_module._sync_shipped_skills_on_pipeline_run
    original_warn = run_module._warn_if_capabilities_degraded
    for attr_name, stub in stubs.items():
        setattr(run_module, attr_name, stub)

    process_view_calls: list[str] = []

    def stub_process_view(_stack: object) -> None:
        process_view_calls.append("maybe_enter_process_view")

    monkeypatch.setattr(run_module, "_maybe_enter_process_view", stub_process_view)

    try:
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
    finally:
        effect_executor_module.execute_agent_effect = original_executor_module
        run_module._load_configuration = original_load
        run_module._run_preflight_checks = original_preflight
        run_module._sync_shipped_skills_on_pipeline_run = original_sync
        run_module._warn_if_capabilities_degraded = original_warn
        run_module._run_project_policy_readiness = original_readiness
        run_module._execute_pipeline = original_execute

    assert rc == 0, "NORMAL-mode run must continue into the development pipeline"
    assert process_view_calls == ["maybe_enter_process_view"], (
        "run_pipeline must retain the post-readiness process-view boundary"
    )
    assert working_tree_snapshots, "policy readiness must use the injected working-tree seam"
    assert policy_commit_requests, "policy readiness must use the injected policy-commit seam"
    _assert_handback_state_clean(
        phase=phase,
        preflight_order=preflight_order,
        policy_phase_session_ids=policy_phase_session_ids,
        captured_pipeline_state=captured_pipeline_state,
        captured_first_effects=captured_first_effects,
    )


def _ensure_run_func_state_unset() -> None:
    run_module._state.run_func = run_module._RUN_FUNC_UNSET


def _stub_load_result_for_handback(workspace_root: str, *, phase: str) -> run_module._LoadResult:
    config = UnifiedConfig()
    workspace_scope = WorkspaceScope(root=workspace_root, allowed_roots=[workspace_root])
    return run_module._LoadResult(
        config=config,
        workspace_scope=workspace_scope,
        initial_state=PipelineState(
            phase=phase,
            policy_entry_phase=phase,
        ),
        policy_bundle=None,
        run_id="test-run-id",
    )
