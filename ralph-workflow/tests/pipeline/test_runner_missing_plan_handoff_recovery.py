"""Tests for MissingPlanHandoffError recovery from any non-planning phase.

When a non-planning phase requires a plan handoff at .agent/PLAN.md and the
handoff is missing, ``prepare_prompt`` raises ``MissingPlanHandoffError``.
The runner must catch that exception and re-route the pipeline back to
``pipeline_policy.entry_phase`` (compiled at policy load time to ``"planning"``
for the default policy) for ANY phase that triggers it, not only
``failed_route``.

Mirrors the construction pattern from
``tests/test_handle_inline_effect_phase_entry_clearing.py``: same imports,
same ``load_policy`` + ``_load_default_policy_bundle`` helper, same
``runner_module.handle_inline_effect`` invocation. The test uses
``MemoryWorkspace`` so the runner's ``ckpt.save`` writes only to the
in-process storage, and a ``tmp_path``-rooted ``checkpoint_path`` so the
real on-disk ``checkpoint.json`` is never touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import _runner_state_helpers
from ralph.pipeline import progress as progress_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models._recovery_policy import RecoveryPolicy
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy


def _load_default_policy_bundle() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    return bundle.pipeline, bundle.artifacts


class TestRunnerMissingPlanHandoffRecovery:
    """MissingPlanHandoffError from any non-planning phase routes to entry phase."""

    @pytest.mark.timeout_seconds(15)
    def test_handle_inline_effect_recovers_missing_plan_handoff_from_development_phase(
        self, tmp_path: Path
    ) -> None:
        """Development phase with no .agent/PLAN.md re-routes to planning.

        Asserts:
            - return value is a PipelineState (not an int / exception)
            - recovered_state.phase == PipelinePhase.PLANNING
            - recovered_state.recovery_epoch == 1
            - recovered_state.last_error contains "MissingPlanHandoffError"
        """
        pipeline_policy, artifacts_policy = _load_default_policy_bundle()

        # The pre-compiled entry_phase must be "planning" for the default policy.
        assert pipeline_policy.entry_phase == "planning", (
            "Default pipeline must compile entry_phase='planning' "
            "(defaults/pipeline.toml entry_block='developer_iteration' → first child phase)."
        )

        root = tmp_path
        workspace_scope = WorkspaceScope(
            root=root, allowed_roots=frozenset([root])
        )

        # No plan handoff exists at .agent/PLAN.md inside MemoryWorkspace.
        effect = PreparePromptEffect(
            phase="development",
        )
        state = PipelineState(
            phase="development",
            previous_phase=None,
            checkpoint_saved_count=0,
            recovery_epoch=0,
        )

        result = runner_module.handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
            agents_policy=None,
            registry=None,
            config=None,
            display=None,
            workspace_scope=workspace_scope,
        )

        # (a) the return value is a PipelineState
        assert isinstance(result, PipelineState), (
            f"handle_inline_effect must return a PipelineState, got {type(result).__name__}"
        )

        # (b) state.phase advances to planning (entry_phase)
        assert result.phase == "planning", (
            f"Recovered phase must be planning (entry_phase), got {result.phase}"
        )

        # (c) recovery_epoch increments by 1
        assert result.recovery_epoch == 1, (
            f"recovery_epoch must be 1 after one recovery, got {result.recovery_epoch}"
        )

        # (d) last_error captures the underlying MissingPlanHandoffError message
        assert "plan handoff" in (result.last_error or ""), (
            f"last_error must describe the plan handoff failure, got {result.last_error!r}"
        )

    @pytest.mark.timeout_seconds(15)
    def test_recover_helper_routes_to_failed_route_when_cycle_cap_exceeded(
        self, tmp_path: Path
    ) -> None:
        """When recovery_epoch >= cycle_cap, helper routes to failed_route.

        Builds a PipelinePolicy with ``recovery.cycle_cap=3`` so the test
        exercises the bound-exceeded path in milliseconds. Asserts:

        - the recovered state advances to ``pipeline_policy.recovery.failed_route``
          (``'failed_terminal'``) rather than ``entry_phase``,
        - ``recovery_epoch`` is incremented (not capped),
        - ``last_error`` preserves the original exception message so the
          operator still sees the underlying MissingPlanHandoffError.
        """
        pipeline_policy, _ = _load_default_policy_bundle()

        # Override the frozen PipelinePolicy.recovery with a tightened bound
        # (cycle_cap=3) so the test exercises the bound-exceeded branch while
        # failing in milliseconds. failed_route stays 'failed_terminal' so the
        # routing target is the conventional terminal-failure phase.
        override_recovery = RecoveryPolicy(
            cycle_cap=3,
            failed_route="failed_terminal",
            terminal_failure_phase=None,
            preserve_session_on_categories=("agent",),
        )
        pipeline_policy = pipeline_policy.model_copy(
            update={"recovery": override_recovery}
        )

        # Sanity: the default policy's recovery.cycle_cap is 200; prove we
        # overrode it so the test exercises the bound, not the default.
        assert pipeline_policy.recovery.cycle_cap == 3

        state = PipelineState(
            phase="development",
            recovery_epoch=3,
            last_error=None,
        )

        result = _runner_state_helpers.recover_missing_plan_handoff(
            state=state,
            pipeline_policy=pipeline_policy,
            checkpoint_path=tmp_path / "ckpt.json",
            subscriber=None,
            exc=MissingPlanHandoffError("test missing plan handoff"),
        )

        # (a) phase routes to failed_route ('failed_terminal'), NOT entry_phase
        assert result.phase == "failed_terminal", (
            f"Bound-exceeded recovery must route to failed_route "
            f"('failed_terminal'), got phase={result.phase!r}"
        )
        assert result.phase != pipeline_policy.entry_phase, (
            "Bound-exceeded recovery must NOT route back to entry_phase"
        )

        # (b) recovery_epoch is incremented (not capped) from 3 -> 4
        assert result.recovery_epoch == 4, (
            f"recovery_epoch must be incremented past cycle_cap (not capped), "
            f"got {result.recovery_epoch}"
        )

        # (c) last_error preserves the underlying exc message on the
        # bound-exceeded path (matches the ExitFailureEffect convention).
        assert "plan handoff" in (result.last_error or ""), (
            f"last_error must preserve the underlying MissingPlanHandoffError "
            f"on the bound-exceeded path, got {result.last_error!r}"
        )

    @pytest.mark.timeout_seconds(15)
    def test_advance_phase_resets_recovery_epoch_to_zero_for_forward_progress(
        self,
    ) -> None:
        """``progress.advance_phase`` resets ``recovery_epoch`` on normal forward progress.

        Pins the analysis-feedback correctness fix: without the reset, a
        ``recovery_epoch`` accumulated by an earlier missing-plan recovery
        loop carries across successful forward transitions and inflates
        ``pipeline_policy.recovery.cycle_cap`` lifetime consumption. A later
        missing-plan recovery loop would then hard-fail solely because the
        earlier (already-recovered) loop consumed the budget.

        The fix resets ``recovery_epoch`` to ``0`` inside
        ``progress.advance_phase`` so the missing-plan recovery counter is
        scoped to the CURRENT consecutive recovery loop. Callers that need
        to keep a non-zero ``recovery_epoch`` (e.g.
        ``recover_missing_plan_handoff`` and ``_advance_to_failed``) set it
        explicitly via ``copy_with`` AFTER calling ``advance_phase``, so
        the reset is invisible to the recovery bookkeeping contract and
        visible to every other forward-progress path.

        Asserts:

        - ``advance_phase`` from a state with ``recovery_epoch=N>0``
          returns a state with ``recovery_epoch=0`` (forward progress
          ends the recovery loop scope).
        - Calling ``advance_phase`` AGAIN on the returned state (forward
          progress keeps flowing) keeps ``recovery_epoch=0``.
        - The fix is implemented in ``progress.advance_phase`` itself, NOT
          by callers, so all downstream callers (loopback, reducer,
          loopback.py) inherit the reset without modification.
        """
        pipeline_policy, _ = _load_default_policy_bundle()

        # A state whose ``recovery_epoch`` is non-zero to simulate a
        # pipeline that has previously been through a missing-plan
        # recovery loop (e.g. ``recovery_epoch=5``). The default policy's
        # cycle_cap is 200, so 5 is well below the bound.
        primed_state = PipelineState(
            phase="planning",
            previous_phase="development",
            recovery_epoch=5,
        )
        assert primed_state.recovery_epoch == 5

        # Normal forward advance resets ``recovery_epoch`` to ``0``
        # regardless of how many prior recoveries happened.
        advanced = progress_module.advance_phase(
            primed_state,
            "development",
            policy=pipeline_policy,
        )

        assert advanced.recovery_epoch == 0, (
            "progress.advance_phase MUST reset recovery_epoch to 0 on "
            "normal forward progress so cycle_cap is scoped to the "
            f"CURRENT consecutive recovery loop; got {advanced.recovery_epoch}"
        )
        assert advanced.phase == "development", (
            "advance_phase must still advance the phase as expected"
        )

        # A second forward advance keeps the reset at ``0`` (no
        # re-priming) so the operator's missing-plan recovery budget
        # starts fresh after every forward transition.
        second_advance = progress_module.advance_phase(
            advanced,
            "development_analysis",
            policy=pipeline_policy,
        )
        assert second_advance.recovery_epoch == 0, (
            "Subsequent advance_phase calls must keep recovery_epoch at 0 "
            "until a missing-plan recovery re-primes it; "
            f"got {second_advance.recovery_epoch}"
        )

    @pytest.mark.timeout_seconds(15)
    def test_recover_helper_after_forward_progress_starts_fresh_budget(
        self, tmp_path: Path
    ) -> None:
        """A later missing-plan incident starts with a fresh budget after forward progress.

        End-to-end regression for the analysis-feedback correctness bug:
        a pipeline that RECOVERED from a missing-plan handoff, then made
        forward progress, then ENCOUNTERED ANOTHER missing-plan handoff,
        must start the second recovery loop with ``recovery_epoch=0``
        (not carry forward the N from the first loop). Without the reset,
        the second loop starts with the inflated epoch and could
        hard-fail solely because the earlier (already-recovered) loop
        consumed the cap.

        The fix (recovery_epoch reset in ``progress.advance_phase``)
        ensures the recovery budget is scoped to the CURRENT consecutive
        missing-plan recovery loop. The test simulates the full lifecycle
        via ``progress.advance_phase`` + the real
        ``recover_missing_plan_handoff`` helper, then asserts both
        recoveries observe the contract:

        - First recovery: starts at ``recovery_epoch=0``, ends at ``1``.
        - Forward advance: resets to ``0``.
        - Second recovery: starts at ``recovery_epoch=0`` (fresh budget),
          ends at ``1``.
        """
        pipeline_policy, _ = _load_default_policy_bundle()

        # First missing-plan recovery loop: development -> planning
        state_at_first_recovery = PipelineState(
            phase="development",
            previous_phase=None,
            recovery_epoch=0,
        )
        first_recovery = _runner_state_helpers.recover_missing_plan_handoff(
            state=state_at_first_recovery,
            pipeline_policy=pipeline_policy,
            checkpoint_path=tmp_path / "ckpt.json",
            subscriber=None,
            exc=MissingPlanHandoffError("first missing plan"),
        )
        assert first_recovery.phase == pipeline_policy.entry_phase
        assert first_recovery.recovery_epoch == 1, (
            f"First recovery must end at recovery_epoch=1, "
            f"got {first_recovery.recovery_epoch}"
        )

        # Forward progress between the two recovery loops: planning ->
        # development. This must reset recovery_epoch to 0 so the second
        # incident starts with a fresh budget.
        advanced = progress_module.advance_phase(
            first_recovery,
            "development",
            policy=pipeline_policy,
        )
        assert advanced.recovery_epoch == 0, (
            "Forward progress between recovery loops MUST reset "
            "recovery_epoch to 0; got "
            f"{advanced.recovery_epoch}"
        )

        # Second missing-plan recovery loop: the same helper, called
        # again with a state whose recovery_epoch is now 0 (NOT carried
        # forward from the first loop). This is the contract: every
        # recovery loop starts fresh.
        second_recovery = _runner_state_helpers.recover_missing_plan_handoff(
            state=advanced,
            pipeline_policy=pipeline_policy,
            checkpoint_path=tmp_path / "ckpt.json",
            subscriber=None,
            exc=MissingPlanHandoffError("second missing plan"),
        )
        assert second_recovery.phase == pipeline_policy.entry_phase
        assert second_recovery.recovery_epoch == 1, (
            "Second recovery must observe a fresh budget: recovery_epoch "
            "starts at 0 (post forward progress) and ends at 1; got "
            f"{second_recovery.recovery_epoch}"
        )
        assert second_recovery.phase == first_recovery.phase, (
            "Second recovery must route to the same entry_phase as the "
            "first recovery (default policy entry_phase='planning')"
        )

    @pytest.mark.timeout_seconds(15)
    def test_recover_helper_resets_entry_phase_chain_on_success_path(
        self, tmp_path: Path
    ) -> None:
        """Success-path recovery resets the entry phase's ``AgentChainState``.

        Regression for the analysis-feedback correctness bug: prior to
        the fix, ``recover_missing_plan_handoff`` routed back to
        ``pipeline_policy.entry_phase`` WITHOUT resetting that phase's
        ``AgentChainState``. A planning chain that had advanced to
        ``current_index=1`` (fallback planner) and accumulated
        ``retries=2`` was preserved verbatim on the recovered state, so
        ``ralph/pipeline/orchestrator.py::_current_agent_name`` resumed
        on the fallback planner with retry debt instead of starting
        fresh from ``current_index=0, retries=0`` like the explicit
        failed-route re-entry branch in ``ralph/pipeline/runner.py``.

        The fix calls ``reset_phase_chain_for_recovery`` on the
        success path so the recovered entry phase starts a fresh
        ``AgentChainState`` matching the failed-route re-entry
        behaviour. The bound-exceeded path (``failed_route``) does
        NOT reset the chain because the pipeline is heading to a
        terminal phase where the planning chain is irrelevant.

        Asserts:

        - A state primed with a planning chain at
          ``current_index=1`` and ``retries=2`` recovers to a state
          whose ``planning`` chain has ``current_index=0`` and
          ``retries=0``.
        - The recovered chain still carries the original ``agents``
          list (the chain identity is preserved; only the index and
          retry counter reset).
        - The chain reset does NOT affect the bound-exceeded path:
          a recovery with ``recovery_epoch >= cycle_cap`` that
          routes to ``failed_route`` does NOT mutate the planning
          chain (because that branch never re-enters planning).
        """
        pipeline_policy, _ = _load_default_policy_bundle()

        # The planning agent chain is sourced from the AgentsPolicy on the
        # PolicyBundle (not the PipelinePolicy itself). Resolve the
        # chain by the entry_phase key so the regression test mirrors
        # the production plumbing.
        bundle_defaults_dir = (
            Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        )
        full_bundle = load_policy(bundle_defaults_dir)
        planning_agents = full_bundle.agents.agent_chains[
            pipeline_policy.entry_phase
        ].agents

        primed_state = PipelineState(
            phase="development",
            previous_phase=None,
            recovery_epoch=0,
            phase_chains={
                pipeline_policy.entry_phase: AgentChainState(
                    agents=planning_agents,
                    current_index=1,
                    retries=2,
                ),
            },
        )

        primed_chain = primed_state.chain_for_phase(
            pipeline_policy.entry_phase
        )
        assert primed_chain is not None, (
            "primed_chain must exist for the entry_phase before recovery"
        )
        assert primed_chain.current_index == 1, (
            f"primed_chain.current_index must be 1 (regression seed); "
            f"got {primed_chain.current_index}"
        )
        assert primed_chain.retries == 2, (
            f"primed_chain.retries must be 2 (regression seed); "
            f"got {primed_chain.retries}"
        )

        recovered = _runner_state_helpers.recover_missing_plan_handoff(
            state=primed_state,
            pipeline_policy=pipeline_policy,
            checkpoint_path=tmp_path / "ckpt.json",
            subscriber=None,
            exc=MissingPlanHandoffError("missing plan handoff"),
        )

        assert recovered.phase == pipeline_policy.entry_phase, (
            f"Success-path recovery must route to entry_phase "
            f"({pipeline_policy.entry_phase!r}); got {recovered.phase!r}"
        )

        recovered_chain = recovered.chain_for_phase(
            pipeline_policy.entry_phase
        )
        assert recovered_chain is not None, (
            "recovered planning chain must exist on the recovered state"
        )
        assert recovered_chain.current_index == 0, (
            "recovered planning chain must have current_index=0; "
            "without the reset, the chain preserves current_index=1 and "
            f"resumes on the fallback planner. got {recovered_chain.current_index}"
        )
        assert recovered_chain.retries == 0, (
            "recovered planning chain must have retries=0; "
            "without the reset, the chain preserves retries=2 and "
            f"resumes with retry debt. got {recovered_chain.retries}"
        )
        assert tuple(recovered_chain.agents) == tuple(planning_agents), (
            "Chain reset MUST preserve the original agents list (only "
            "current_index and retries reset); agents list changed: "
            f"{recovered_chain.agents!r} != {planning_agents!r}"
        )

    @pytest.mark.timeout_seconds(15)
    def test_recover_helper_does_not_reset_chain_on_bound_exceeded_path(
        self, tmp_path: Path
    ) -> None:
        """Bound-exceeded recovery does NOT touch the planning chain.

        The bound-exceeded path routes to ``failed_route`` (a terminal
        phase), so the planning chain state is irrelevant on that
        branch and the reset must NOT fire (otherwise it would
        needlessly mutate state that will never be read again).

        Asserts: with ``recovery_epoch=3 >= cycle_cap=3`` and a primed
        planning chain at ``current_index=1, retries=2``, the recovered
        state routes to ``failed_route`` and the planning chain is
        preserved verbatim (current_index and retries unchanged).
        """
        pipeline_policy, _ = _load_default_policy_bundle()

        override_recovery = RecoveryPolicy(
            cycle_cap=3,
            failed_route="failed_terminal",
            terminal_failure_phase=None,
            preserve_session_on_categories=("agent",),
        )
        pipeline_policy = pipeline_policy.model_copy(
            update={"recovery": override_recovery}
        )

        # The planning agent chain is sourced from the AgentsPolicy on the
        # PolicyBundle (not the PipelinePolicy itself). Resolve the
        # chain by the entry_phase key so the regression test mirrors
        # the production plumbing.
        bundle_defaults_dir = (
            Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        )
        full_bundle = load_policy(bundle_defaults_dir)
        planning_agents = full_bundle.agents.agent_chains[
            pipeline_policy.entry_phase
        ].agents

        primed_state = PipelineState(
            phase="development",
            previous_phase=None,
            recovery_epoch=3,
            last_error=None,
            phase_chains={
                pipeline_policy.entry_phase: AgentChainState(
                    agents=planning_agents,
                    current_index=1,
                    retries=2,
                ),
            },
        )

        recovered = _runner_state_helpers.recover_missing_plan_handoff(
            state=primed_state,
            pipeline_policy=pipeline_policy,
            checkpoint_path=tmp_path / "ckpt.json",
            subscriber=None,
            exc=MissingPlanHandoffError("missing plan handoff"),
        )

        assert recovered.phase == "failed_terminal", (
            f"Bound-exceeded recovery must route to failed_route "
            f"('failed_terminal'); got {recovered.phase!r}"
        )

        bound_chain = recovered.chain_for_phase(
            pipeline_policy.entry_phase
        )
        assert bound_chain is not None, (
            "Bound-exceeded recovery MUST NOT clear the planning chain; "
            "the chain is irrelevant on the failed_route branch but the "
            "reset must not fire on that branch"
        )
        assert bound_chain.current_index == 1, (
            "Bound-exceeded recovery MUST NOT reset the planning chain; "
            "without the path-gate, the chain would be wrongly reset to "
            f"current_index=0. got {bound_chain.current_index}"
        )
        assert bound_chain.retries == 2, (
            "Bound-exceeded recovery MUST NOT reset the planning chain; "
            "without the path-gate, the chain would be wrongly reset to "
            f"retries=0. got {bound_chain.retries}"
        )

