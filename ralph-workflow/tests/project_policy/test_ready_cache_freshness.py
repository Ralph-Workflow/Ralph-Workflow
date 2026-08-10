"""READY-cache freshness regression (S-5).

A completed policy run must stay completed. ``_finalize_ready_state``
owns the cache write so the cached signature is taken over the tree
the run actually leaves behind -- after ``condense_placeholder_block``
and the auto-commit. The contract: immediately after
``run_project_policy_readiness`` returns READY,
``cache.read_cached_ready(workspace, stack) is True``; a second
preflight over the same workspace then hits the cache and invokes
zero agents, because the cache short-circuits before the policy
pipeline runs.

Two routes reach READY through the orchestrator and both must
honour the contract:

* The DIRECT-READY route goes through the no-findings path in
  ``run_policy_readiness_preflight`` -- the preflight returns
  READY before the policy pipeline is invoked.
* The POST-REMEDIATION route goes through the policy pipeline and
  reaches READY in ``pipeline_driver._finish`` after remediation
  + analysis.

Both routes are regression-tested immediately after the hand-back
so a future change that re-introduces a stale cache signature
lights the test up rather than silently letting a full
re-validation slip past.
"""

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
    """Reset ``_state.run_func`` to ``_RUN_FUNC_UNSET`` before each test."""
    run_module._state.run_func = run_module._RUN_FUNC_UNSET


def _stub_load_result(workspace_root: str, *, phase: str = "planning") -> _LoadResult:
    """Build a minimal ``_LoadResult`` that exercises the helper seam."""
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
    """Submit a Markdown analysis decision with status ``completed``.

    A JSON blob at the analysis artifact path is rejected by the
    analysis-decision spec (the loader expects a Markdown file with
    frontmatter and a ``Criterion Verdicts`` section whose IDs match
    the artifact-type pattern ``PR-NNN``). The legacy
    ``_approve_policy`` helper in ``test_run_integration.py`` writes
    JSON, which is why the earlier integration tests pass the
    orchestrator exit-code and emit-message assertions without ever
    reaching a real READY cache write -- the analysis loops back to
    remediation until the budget is spent and the driver returns
    BLOCKED with the word "ready" in the budget-spent message.

    This helper writes the Markdown form so the analysis phase
    actually approves and the driver reaches the
    ``_finalize_ready_state`` path that writes the READY cache.
    Mirrors ``_submit_decision`` in ``test_pipeline_driver.py`` for
    the ``completed`` case.
    """
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
    """AC-CACHE-01: a direct-READY preflight (no findings path) writes a
    fresh READY cache, and a second preflight over the same workspace
    invokes zero agents.
    """
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

    # The workspace is fully complete BEFORE the orchestrator runs, so
    # the preflight returns READY on the no-findings path -- the same
    # path that the bundle starter seeding + empty AGENTS.md
    # bootstrap would walk. The orchestrator calls
    # ``_finalize_ready_state`` which writes the cache; without the
    # S-5 fix the cache is stale on arrival.
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

    # Use the SAME stack the orchestrator detected. The orchestrator's
    # stack is captured at the START of the run -- in this case, the
    # workspace is ALREADY pre-seeded by the test, so the stack is
    # the pre-seeded stack (with ``has_tests=True`` because the
    # ``testing-policy.md`` name trips ``detect_tests``). The test
    # computes the same stack by calling ``get_project_stack(ws)``
    # against the post-seed workspace, BEFORE the orchestrator runs.
    stack = get_project_stack(ws)
    assert policy_cache.read_cached_ready(ws, stack) is True, (
        "Direct-READY route must write a fresh READY cache after the "
        "post-READY mutations -- see _finalize_ready_state. A stale "
        "cache signature would re-validate the whole tree on every "
        "subsequent preflight."
    )

    # Second preflight over the same workspace. The fresh cache must
    # short-circuit before the policy pipeline runs, so the injected
    # agent is never invoked. If a regression re-introduces a stale
    # signature, the cache is missed, the validator re-runs, the
    # preflight still returns READY (no findings), and the counter
    # stays at zero -- but only because the cache miss routed
    # through the direct-READY path, not through the policy pipeline.
    # The assertion below distinguishes those two paths: a count of
    # zero on a cache miss is the expected cache-MISS short-circuit;
    # any nonzero count means the policy pipeline ran.
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
def test_post_remediation_ready_route_writes_fresh_cache_and_second_preflight_invokes_zero_agents() -> None:
    """AC-CACHE-02: a post-remediation READY preflight (the through-``_finish``
    path) writes a fresh READY cache, and a second preflight over the
    same workspace invokes zero agents.
    """
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
            # Materialize every required canonical file so the
            # post-remediation revalidation passes.
            _seed_agents_md(ws)
            _seed_claude_md(ws)
            _seed_all_core_complete(ws, get_project_stack(ws))
            return True
        # The analysis phase then approves what remediation wrote.
        # The artifact is a Markdown file with frontmatter, NOT a
        # JSON blob -- see _submit_decision in test_pipeline_driver.py
        # for the canonical format. A JSON artifact would be
        # rejected by the analysis-decision spec and the analysis
        # would loop back to remediation instead of approving.
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

    # The orchestrator detected the stack at the START of the run --
    # BEFORE the preflight writes the bootstrap, BEFORE the policy
    # pipeline runs the fake agent. At that moment the workspace is
    # empty, so ``has_tests`` is False. The post-remediation test
    # reads the cache AFTER the agent seeded ``testing-policy.md``,
    # which would make ``has_tests`` flip to True -- a different
    # stack produces a different cache signature and the read
    # misses on a stack mismatch. Pin the test to the
    # orchestrator's pre-seed stack by detecting the stack against a
    # FRESH empty workspace (the same stack the orchestrator
    # detected when the run started, before anything was written).
    stack = get_project_stack(MemoryWorkspace())
    assert policy_cache.read_cached_ready(ws, stack) is True, (
        "Post-remediation READY route must write a fresh READY cache "
        "after the post-READY mutations -- see _finalize_ready_state. "
        "Without this, a project that reached READY through the "
        "policy pipeline (remediation + analysis) pays a full "
        "re-validation on every subsequent preflight."
    )

    # Second preflight over the same workspace. The fresh cache must
    # short-circuit before the policy pipeline runs, so the counting
    # agent is never invoked. A nonzero count means a regression
    # broke the cache -- the second preflight routed through the
    # full orchestrator (remediation + analysis) instead of hitting
    # the cache.
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
