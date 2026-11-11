from __future__ import annotations

import pytest

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.state import PipelineState
from ralph.project_policy import analysis as policy_analysis
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

_LoadResult = run_module._LoadResult


def _ensure_run_func_state_unset() -> None:
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


def _submit_completed_decision(ws: MemoryWorkspace) -> None:
    ws.mkdirs(".agent/artifacts")
    ws.write(
        policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
        (
            "---\n"
            f"type: {policy_analysis.ANALYSIS_ARTIFACT_TYPE}\n"
            "status: completed\n"
            "---\n\n"
            "## Summary\n\n"
            "- [SUM-1] Review complete. Evidence: declared policy probes were inspected.\n"
            "\n## Criterion Verdicts\n\n"
            "- [PR-001] Criterion: declared policy fact holds. Expected observation: the declared probe resolves. Verdict: met. Evidence: declared policy probes were inspected. Location: policy declaration.\n"
        ),
    )


@pytest.mark.timeout_seconds(5)
def test_direct_ready_route_writes_fresh_cache_and_second_preflight_invokes_zero_agents() -> None:
    _ensure_run_func_state_unset()

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/ready-direct-cache")

    from ralph.language_detector import get_project_stack
    from ralph.project_policy import cache as policy_cache
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, get_project_stack(ws))

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=lambda _m: None,
    )
    assert rc == 0, "direct-READY preflight should exit 0"

    stack = get_project_stack(ws)
    assert policy_cache.read_cached_ready(ws, stack) is True, (
        "Direct-READY route must write a fresh READY cache after the "
        "post-READY mutations -- see _finalize_ready_state. A stale "
        "cache signature would re-validate the whole tree on every "
        "subsequent preflight."
    )

    second_agent_invocations: list[str] = []

    def counting_invoke(*, phase: str, prompt_path: str) -> bool:
        del prompt_path
        second_agent_invocations.append(phase)
        return True

    rc2 = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=lambda _m: None,
        invoke_remediation_agent_factory=lambda _w: counting_invoke,
    )
    assert rc2 == 0
    assert second_agent_invocations == [], (
        f"second preflight over the same workspace invoked agents "
        f"{second_agent_invocations!r}; the change-aware cache must "
        "short-circuit before the policy pipeline runs."
    )


@pytest.mark.timeout_seconds(5)
def test_post_remediation_ready_route_writes_fresh_cache_and_second_preflight_invokes_zero_agents() -> (
    None
):
    _ensure_run_func_state_unset()

    ws = MemoryWorkspace()
    load_result = _stub_load_result("/test/ready-post-remediation-cache")

    from ralph.language_detector import get_project_stack
    from ralph.project_policy import cache as policy_cache
    from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    def fix_invoke(*, phase: str, prompt_path: str) -> bool:
        del prompt_path
        if phase == PHASE_REMEDIATION:
            _seed_agents_md(ws)
            _seed_claude_md(ws)
            _seed_all_core_complete(ws, get_project_stack(ws))
            return True
        _submit_completed_decision(ws)
        return True

    rc = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=lambda _m: None,
        invoke_remediation_agent_factory=lambda _w: fix_invoke,
    )
    assert rc == 0, "post-remediation READY preflight should exit 0"

    stack = get_project_stack(MemoryWorkspace())
    assert policy_cache.read_cached_ready(ws, stack) is True, (
        "Post-remediation READY route must write a fresh READY cache "
        "after the post-READY mutations -- see _finalize_ready_state. "
        "Without this, a project that reached READY through the "
        "policy pipeline (remediation + analysis) pays a full "
        "re-validation on every subsequent preflight."
    )

    second_agent_invocations: list[str] = []

    def counting_invoke(*, phase: str, prompt_path: str) -> bool:
        del prompt_path
        second_agent_invocations.append(phase)
        return True

    rc2 = run_module._run_project_policy_readiness(
        load_result=load_result,
        display_context=make_display_context(),
        workspace_factory=lambda: ws,
        emit_factory=lambda _m: None,
        invoke_remediation_agent_factory=lambda _w: counting_invoke,
    )
    assert rc2 == 0
    assert second_agent_invocations == [], (
        f"second preflight over the same workspace invoked agents "
        f"{second_agent_invocations!r}; the change-aware cache must "
        "short-circuit before the policy pipeline runs on a fresh "
        "cache hit."
    )
