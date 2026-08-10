"""The policy preflight must not leak agent state into the pipeline.

The policy preflight runs at startup (Phase 2c of ``run_pipeline``) and routes
its agent invocations through the same :func:`execute_agent_effect` that the
pipeline runner uses. ``execute_agent_effect`` publishes the successful
attempt's session id into a thread-local the pipeline runner consumes via
:func:`apply_session_capture` after every :class:`InvokeAgentEffect`.

Without a boundary at the out-of-graph call site, the remediation session id
sits in the thread-local when Phase 4 (``_execute_pipeline``) starts, and the
first agent invocation in the pipeline picks it up. A planning attempt that
does not itself publish a session id inherits the remediation session id and
resumes the remediation conversation instead of planning.

This suite pins the contract: after ``run_project_policy_readiness`` returns,
the thread-local the pipeline runner reads must be empty for the policy phase
that just ran. A failure here means a freshly loaded :class:`PipelineState`
fed through :func:`apply_session_capture` would carry the remediation session
id forward, and the development run would resume the wrong conversation.

The fix lives at the out-of-graph boundary in
:mod:`ralph.project_policy.cli_integration` -- the policy phases snapshot
and restore the thread-locals around each policy agent invocation so the
drain that the pipeline runner owns is untouched by work that is not part of
the pipeline graph.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
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
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration
from ralph.project_policy.policy_mode import PolicyMode
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps
    from ralph.workspace.protocol import Workspace

#: Sentinel for tests that need a PipelineEvent-shaped return without
#: importing the untyped MCP bridge. Compared by ``==`` at call sites.
_agent_success_sentinel: PipelineEvent = PipelineEvent.AGENT_SUCCESS

#: Identifier for the leaked session id that a fake ``execute_agent_effect``
#: publishes in the thread-local. Distinct from any real session id the
#: pipeline runner might generate so the failure message names the offender.
LEAKED_POLICY_SESSION_ID: str = "policy-remediation-leaked-session"


def _seed_workspace_with_complete_policy(ws: MemoryWorkspace) -> None:
    """Seed a complete policy so the deterministic validator passes."""
    from ralph.language_detector.models import ProjectStack
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))


def _approve_policy_in_workspace(ws: MemoryWorkspace) -> None:
    """Submit a 'completed' analysis decision so the remediation flow closes."""
    import json

    from ralph.project_policy import analysis as policy_analysis

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


def _build_load_result(
    *,
    workspace_root: str,
    policy_bundle: object | None = None,
) -> run_module._LoadResult:
    """Build a minimal ``_LoadResult`` with a real bundle so the closure runs."""
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
    """The contract, observed.

    The policy preflight invokes the production
    :func:`_make_production_invoke_agent` closure with a real bundle. The
    fake :func:`execute_agent_effect` simulates the success path of
    :func:`execute_agent_effect` -- exactly what
    :func:`_record_successful_attempt_session` does when an agent invocation
    succeeds -- by calling :func:`set_last_captured_session_id` with a
    distinctive session id and :func:`_set_last_captured_retry_intent` with
    a cleared intent. After the preflight returns, the pipeline runner's
    consumer (:func:`apply_session_capture`) is asked to drain whatever sits
    in the thread-locals into a fresh :class:`PipelineState`.

    Without the fix, the drained state carries the leaked session id
    forward: a planning attempt that does not itself publish a session id
    would resume the remediation conversation. The test asserts the drain
    is empty.
    """
    bundle = load_policy(default_dir())
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
        # The two writes below mirror `_record_successful_attempt_session`
        # at effect_executor.py:146-147 verbatim, so the leak they model
        # is the real production leak, not a synthetic one.
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

    # Drain the same thread-locals the pipeline runner would drain and
    # assert no policy-phase agent state survives.
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

    # `apply_session_capture` is the pipeline runner's actual consumer.
    # Driving it against a fresh PipelineState is the same drain the first
    # pipeline agent effect would perform; assert the resulting state has
    # no last_agent_session_id and a cleared agent_retry_intent.
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


def test_policy_phase_failure_path_does_not_leak_retry_intent() -> None:
    """A failing policy phase that publishes a retry intent must clear it too.

    :func:`_set_last_captured_retry_intent` is called on the failure path
    too (see effect_executor.py:534-535, effect_executor.py:555, and
    effect_executor.py:1473-1475). The hand-back contract is the same:
    nothing a policy phase publishes may reach the pipeline runner. This
    case models the retry-intent surface separately so a fix that only
    clears the session id (and forgets the intent) is caught.
    """
    bundle = load_policy(default_dir())
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
        # Keep the session id slot clean for THIS test; the retry-intent
        # leak is what this assertion isolates.
        set_last_captured_session_id(None)
        return PipelineEvent.AGENT_FAILURE

    original = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = fake_execute_agent_effect
    try:
        load_result = _build_load_result(
            workspace_root="/test/project-policy-handback-failure",
            policy_bundle=bundle,
        )

        # The fake never fixes the policy, so the preflight's success
        # invariant does not apply. We only care that the helper returned
        # and left no leaked state behind.
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


# ---------------------------------------------------------------------------
# Persisted-phase hand-back regression
# ---------------------------------------------------------------------------
# These tests prove the hand-back contract at the ``run_pipeline`` seam with
# the real policy closure in the loop. The leak that motivates them is
# thread-local, not a field on the state ``run_pipeline`` hands down: Phase 4
# passes ``initial_state`` to ``_execute_pipeline`` unchanged (``run.py:925``),
# so asserting on the request would pass while the defect remains. The case
# must observe the capture drain itself.
#
# The fake ``execute_agent_effect`` publishes the same session id a real
# successful attempt would (``policy-remediation-session``, mirroring
# ``_record_successful_attempt_session`` at ``effect_executor.py:146``)
# into the thread-locals. The stub ``_execute_pipeline`` then applies the
# real consumer, ``apply_session_capture(initial_state)`` -- the same call
# ``runner._finalize_agent_invocation`` makes at ``runner.py:1082`` -- and
# records the resulting state. Without the out-of-graph boundary fix in
# ``cli_integration._make_production_invoke_agent``, that recorded state
# carries the leaked policy session id forward, and the run resumes the
# remediation conversation in the wrong phase.

#: Distinctive session id the fake ``execute_agent_effect`` publishes into
#: the thread-local. Distinct from any real session id the pipeline might
#: generate so a failure message names the offender.
LEAKED_POLICY_SESSION_ID: str = "policy-remediation-session"

#: The non-terminal phases the pipeline persists, taken from
#: ``effect_executor._DEFAULT_PHASE_NAMES`` (``effect_executor.py:113-126``)
#: less the terminal ``"complete"`` / ``"failed_terminal"`` phases which
#: are not resumed work.
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
    """Return a fake ``execute_agent_effect`` that mirrors the real leak.

    The two writes inside the closure mirror
    :func:`_record_successful_attempt_session` at
    ``effect_executor.py:146-147`` verbatim: a successful attempt publishes
    a session id into the thread-local and clears the retry intent. Without
    the S-3 fix, this id sits in the thread-local when Phase 4 starts.

    The fake seeds a complete policy in ``ws`` on every invocation so the
    deterministic validator the orchestrator re-runs after each
    remediation agent invocation returns an empty finding list.
    """
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
        _seed_agents_md(ws)
        _seed_claude_md(ws)
        _seed_all_core_complete(ws, ProjectStack(primary_language="Python"))
        return _agent_success_sentinel

    return fake_execute_agent_effect


def _build_handback_pipeline_stubs(
    *,
    load_result: run_module._LoadResult,
    ws: MemoryWorkspace,
    preflight_order: list[str],
    captured_pipeline_state: list[PipelineState],
) -> dict[str, object]:
    """Build the run_module stubs and the Phase 4 capture collector.

    Every run_module collaborator that the handback test patches is
    returned in a dict so the caller can install and restore them as a
    group. ``preflight_order`` records which collaborators fired (in the
    order they fired); ``captured_pipeline_state`` receives the state the
    stub ``_execute_pipeline`` observes after applying the real
    ``apply_session_capture`` consumer.

    The policy preflight stub delegates to the real
    :func:`cli_integration.run_project_policy_readiness` with an injected
    ``MemoryWorkspace`` factory; the production
    ``_make_production_invoke_agent`` closure runs and publishes into the
    thread-locals via the fake ``execute_agent_effect`` patched above.
    """
    from ralph.pipeline._runner_session import apply_session_capture

    def stub_load_configuration(
        *args: object, **kwargs: object
    ) -> run_module._LoadResult:
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
        mode: PolicyMode = PolicyMode.NORMAL,
        workspace_factory: Callable[[], Workspace] | None = None,
        emit_factory: Callable[[str], None] | None = None,
        invoke_remediation_agent_factory: Callable[[Workspace], object] | None = None,
    ) -> int:
        del mode, invoke_remediation_agent_factory
        preflight_order.append("run_project_policy_readiness")
        # Always inject a ``workspace_factory`` returning the
        # in-memory ``MemoryWorkspace`` so no real filesystem access
        # happens under ``/test/...``.
        return cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=display_context,
            workspace_factory=lambda: ws,
            emit_factory=emit_factory,
            is_tty=lambda: False,
        )

    def stub_execute_pipeline(
        *args: object,
        **kwargs: object,
    ) -> int:
        # Phase 4 calls ``_execute_pipeline(request, display_context=...)``,
        # so the request arrives as a positional argument with no kwarg
        # alias. Duck-type access to ``initial_state`` keeps this stub free
        # of any import of the private ``_ExecutePipelineRequest`` NamedTuple.
        candidate: object = args[0] if args else kwargs.get("request")
        initial_state: PipelineState | None = getattr(candidate, "initial_state", None)
        if initial_state is None:
            raise AssertionError("stub_execute_pipeline received no initial_state")
        preflight_order.append("execute_pipeline")
        captured_pipeline_state.append(apply_session_capture(initial_state))
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
) -> None:
    """Assert the hand-back contract: clean drain at the Phase 4 seam.

    For every non-terminal persisted phase, the captured state observed
    at the Phase 4 seam must be the same ``PipelineState`` the load
    result handed in: ``last_agent_session_id is None``, the retry intent
    is cleared, and ``phase`` (with ``policy_entry_phase``) is the
    parameterized persisted phase.
    """
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


@pytest.mark.parametrize("phase", _PERSISTED_NON_TERMINAL_PHASES, ids=_PERSISTED_NON_TERMINAL_PHASES)
def test_run_pipeline_handback_returns_to_persisted_phase_not_policy_session(
    phase: str,
) -> None:
    """After policy preflight returns, the run resumes the persisted phase.

    Drives a NORMAL-mode ``run_pipeline`` whose
    ``_run_project_policy_readiness`` is patched to a wrapper that delegates
    to the real orchestrator (``workspace_factory`` returning a
    ``MemoryWorkspace``, ``emit_factory`` collecting lines, and NO
    ``invoke_remediation_agent_factory`` so the production
    ``_make_production_invoke_agent`` closure runs), with a ``_LoadResult``
    carrying a real bundle from ``load_policy(default_dir())`` so
    ``_build_pipeline_deps`` succeeds. The fake ``execute_agent_effect``
    publishes the canonical leaked session id into the thread-local --
    exactly as ``_record_successful_attempt_session`` would on a real
    successful attempt (``effect_executor.py:146``). The stub
    ``_execute_pipeline`` then applies the real consumer
    ``apply_session_capture(initial_state)`` and records the resulting
    state.

    Asserts that, for every non-terminal persisted phase, the captured
    state observed at the Phase 4 seam is the same ``PipelineState`` the
    load result handed in: ``last_agent_session_id is None``, the retry
    intent is cleared, and ``phase`` (with ``policy_entry_phase``) is the
    parameterized persisted phase -- not the leaked
    ``"policy-remediation-session"`` id and not the remediation drain.

    Reverting the S-3 edit turns every parameter red with the leaked
    session id. Every existing case in the test_run_integration suite still
    passes because ``_stub_load_result`` retains its ``phase="planning"``
    default (a parameter added in S-4 to the existing helper).
    """
    # Restore run_module collaborators at the end of this test so a sibling
    # test in another file that shares the same worker process does not see
    # our patches. ``_restore_run_module_collaborators`` (an autouse fixture
    # in ``test_run_integration.py``) restores the original attrs, but that
    # fixture is scoped to the other module's tests; this test patches the
    # same module attributes and must restore them itself.
    _ensure_run_func_state_unset()

    # Imports deferred so the patch is local to this test.
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.policy.loader import default_dir, load_policy

    # Real bundle so the production closure's chain resolution succeeds.
    real_bundle = load_policy(default_dir())
    ws = MemoryWorkspace()

    # Captured session ids across the policy-phase agent invocations.
    policy_phase_session_ids: list[str | None] = []
    fake_execute_agent_effect = _build_policy_phase_leak_fake(
        effect_executor_module, ws, policy_phase_session_ids
    )

    original_executor_module = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = fake_execute_agent_effect

    # Build a load result that carries the parameterized persisted phase
    # AND a real bundle so the production ``_make_production_invoke_agent``
    # closure's chain resolution works.
    load_result = _stub_load_result_for_handback(f"/test/handback-{phase}", phase=phase)
    load_result = run_module._LoadResult(
        config=load_result.config,
        workspace_scope=load_result.workspace_scope,
        initial_state=load_result.initial_state,
        policy_bundle=real_bundle,
        run_id=load_result.run_id,
    )

    # The state captured at Phase 4 by ``_execute_pipeline``.
    captured_pipeline_state: list[PipelineState] = []
    preflight_order: list[str] = []

    stubs = _build_handback_pipeline_stubs(
        load_result=load_result,
        ws=ws,
        preflight_order=preflight_order,
        captured_pipeline_state=captured_pipeline_state,
    )

    original_readiness = run_module._run_project_policy_readiness
    original_execute = run_module._execute_pipeline
    original_load = run_module._load_configuration
    original_preflight = run_module._run_preflight_checks
    original_sync = run_module._sync_shipped_skills_on_pipeline_run
    original_warn = run_module._warn_if_capabilities_degraded
    for attr_name, stub in stubs.items():
        setattr(run_module, attr_name, stub)

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
    _assert_handback_state_clean(
        phase=phase,
        preflight_order=preflight_order,
        policy_phase_session_ids=policy_phase_session_ids,
        captured_pipeline_state=captured_pipeline_state,
    )


def _ensure_run_func_state_unset() -> None:
    """Reset ``_state.run_func`` to ``_RUN_FUNC_UNSET`` before each test."""
    run_module._state.run_func = run_module._RUN_FUNC_UNSET


def _stub_load_result_for_handback(workspace_root: str, *, phase: str) -> run_module._LoadResult:
    """Mirror :func:`tests.project_policy.test_run_integration._stub_load_result`.

    Defined here so the hand-back test does not depend on a private helper
    from another test module. Mirrors the production helper's body, which
    was extended in S-4 to accept a ``phase`` parameter.
    """
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
