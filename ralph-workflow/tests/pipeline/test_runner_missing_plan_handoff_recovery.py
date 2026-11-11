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
    @pytest.mark.timeout_seconds(15)
    def test_handle_inline_effect_recovers_missing_plan_handoff_from_development_phase(
        self, tmp_path: Path
    ) -> None:
        pipeline_policy, artifacts_policy = _load_default_policy_bundle()

        assert pipeline_policy.entry_phase == "planning", (
            "Default pipeline must compile entry_phase='planning' "
            "(defaults/pipeline.toml entry_block='developer_iteration' → first child phase)."
        )

        root = tmp_path
        workspace_scope = WorkspaceScope(root=root, allowed_roots=frozenset([root]))

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

        assert isinstance(result, PipelineState), (
            f"handle_inline_effect must return a PipelineState, got {type(result).__name__}"
        )

        assert result.phase == "planning", (
            f"Recovered phase must be planning (entry_phase), got {result.phase}"
        )

        assert result.recovery_epoch == 1, (
            f"recovery_epoch must be 1 after one recovery, got {result.recovery_epoch}"
        )

        assert "plan handoff" in (result.last_error or ""), (
            f"last_error must describe the plan handoff failure, got {result.last_error!r}"
        )

    @pytest.mark.timeout_seconds(15)
    def test_recover_helper_routes_to_failed_route_when_cycle_cap_exceeded(
        self, tmp_path: Path
    ) -> None:
        pipeline_policy, _ = _load_default_policy_bundle()

        override_recovery = RecoveryPolicy(
            cycle_cap=3,
            failed_route="failed_terminal",
            terminal_failure_phase=None,
            preserve_session_on_categories=("agent",),
        )
        pipeline_policy = pipeline_policy.model_copy(update={"recovery": override_recovery})

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

        assert result.phase == "failed_terminal", (
            f"Bound-exceeded recovery must route to failed_route "
            f"('failed_terminal'), got phase={result.phase!r}"
        )
        assert result.phase != pipeline_policy.entry_phase, (
            "Bound-exceeded recovery must NOT route back to entry_phase"
        )

        assert result.recovery_epoch == 4, (
            f"recovery_epoch must be incremented past cycle_cap (not capped), "
            f"got {result.recovery_epoch}"
        )

        assert "plan handoff" in (result.last_error or ""), (
            f"last_error must preserve the underlying MissingPlanHandoffError "
            f"on the bound-exceeded path, got {result.last_error!r}"
        )

    @pytest.mark.timeout_seconds(15)
    def test_advance_phase_resets_recovery_epoch_to_zero_for_forward_progress(
        self,
    ) -> None:
        pipeline_policy, _ = _load_default_policy_bundle()

        primed_state = PipelineState(
            phase="planning",
            previous_phase="development",
            recovery_epoch=5,
        )
        assert primed_state.recovery_epoch == 5

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
        pipeline_policy, _ = _load_default_policy_bundle()

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
            f"First recovery must end at recovery_epoch=1, got {first_recovery.recovery_epoch}"
        )

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
    def test_recover_helper_resets_entry_phase_chain_on_success_path(self, tmp_path: Path) -> None:
        pipeline_policy, _ = _load_default_policy_bundle()

        bundle_defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        full_bundle = load_policy(bundle_defaults_dir)
        planning_agents = full_bundle.agents.agent_chains[pipeline_policy.entry_phase].agents

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

        primed_chain = primed_state.chain_for_phase(pipeline_policy.entry_phase)
        assert primed_chain is not None, (
            "primed_chain must exist for the entry_phase before recovery"
        )
        assert primed_chain.current_index == 1, (
            f"primed_chain.current_index must be 1 (regression seed); "
            f"got {primed_chain.current_index}"
        )
        assert primed_chain.retries == 2, (
            f"primed_chain.retries must be 2 (regression seed); got {primed_chain.retries}"
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

        recovered_chain = recovered.chain_for_phase(pipeline_policy.entry_phase)
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
        pipeline_policy, _ = _load_default_policy_bundle()

        override_recovery = RecoveryPolicy(
            cycle_cap=3,
            failed_route="failed_terminal",
            terminal_failure_phase=None,
            preserve_session_on_categories=("agent",),
        )
        pipeline_policy = pipeline_policy.model_copy(update={"recovery": override_recovery})

        bundle_defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        full_bundle = load_policy(bundle_defaults_dir)
        planning_agents = full_bundle.agents.agent_chains[pipeline_policy.entry_phase].agents

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

        bound_chain = recovered.chain_for_phase(pipeline_policy.entry_phase)
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
