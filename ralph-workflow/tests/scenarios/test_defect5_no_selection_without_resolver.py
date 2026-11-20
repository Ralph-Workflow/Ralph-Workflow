from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import unmerged_paths
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_record import (
    adopt_active_rebase_record,
    read_record,
)
from ralph.pipeline.auto_integrate_recovery_record import continue_retained_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    FrozenClock,
    make_rebase_stop_resolver,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios._fake_agent_runner import FakeAgentRunner
from tests.scenarios._paused_rebase_fixtures import paused_text_rebase
from tests.scenarios._rebase_recovery_harness_helpers import shared_executor_seam
from tests.scenarios.test_integration_scenario_matrix import (
    REBASE_RESOLUTION_PHASE,
    assert_selected_agent_announcement_implies_launch,
    capture_selection_announcements,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(20),
]

RETAINED_OUTCOME = "rebase resolution retained; continuation resolver required"
RESOLUTION_AGENTS = ("agentA", "agentB")
SELECTION_PREFIX = f"Phase {REBASE_RESOLUTION_PHASE}: Selected agent"


def _scenario_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def test_paused_rebase_without_resolver_retains_work_and_launches_no_agent(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    paused_text_rebase(repo_dir)
    record = adopt_active_rebase_record(repo_dir, "main")
    assert record is not None, "the paused rebase must be adoptable as retained integration work"

    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner):
        from ralph.pipeline.effect_executor import current_agent_effect_dependencies

        controller = current_agent_effect_dependencies().recovery_controller
        assert controller is not None
        controller.register_phase_chain(REBASE_RESOLUTION_PHASE, list(RESOLUTION_AGENTS))
        with capture_selection_announcements() as announcements:
            outcome = continue_retained_resolution(
                repo_dir,
                record,
                _scenario_config(),
                None,
                None,
                recovery_controller=controller,
            )

    assert outcome == RETAINED_OUTCOME, (
        f"a paused rebase with no usable resolver must be retained, got {outcome!r}"
    )
    assert runner.launches == 0, (
        f"no agent may be launched without a usable resolver, got {runner.launches} launches"
    )
    assert not [line for line in announcements if SELECTION_PREFIX in line], (
        "no selection may be announced for the rebase phase without a usable resolver, "
        f"got {[line for line in announcements if SELECTION_PREFIX in line]}"
    )
    selectable = controller.preferred_agent_index(REBASE_RESOLUTION_PHASE, list(RESOLUTION_AGENTS))
    assert selectable.agent == "agentA", (
        "the resolution chain must be selectable so the missing announcement is meaningful"
    )
    persisted = read_record(repo_dir)
    assert persisted is not None
    assert persisted.resolution_gate_key is not None, (
        "the retained work must keep its resolution gate key so the continuation can resume"
    )


def test_selected_agent_announcement_implies_launch(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    paused_text_rebase(repo_dir)

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_scenario_config(),
    )

    with capture_selection_announcements() as announcements:
        attempt_stop_resolution(
            "defect5-announcement-implies-launch",
            StopResolutionTrigger(
                name="rebase_stop",
                root=repo_dir,
                target="main",
                resolver=resolver,
                conflict_config=_scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )

    assert [line for line in announcements if SELECTION_PREFIX in line], (
        "the rebase phase must announce its selection so the coupling is observable"
    )
    assert_selected_agent_announcement_implies_launch(announcements, len(invocations))


def test_nothing_paused_and_nothing_unmerged_launches_no_agent(
    tmp_git_repo: Path,
) -> None:
    repo_dir = tmp_git_repo
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    assert not rebase_in_progress(repo_dir), "the scenario must start with nothing paused"
    assert unmerged_paths(repo_dir) == [], "the scenario must start with nothing unmerged"
    with shared_executor_seam(runner):
        from ralph.pipeline.effect_executor import current_agent_effect_dependencies

        controller = current_agent_effect_dependencies().recovery_controller
        assert controller is not None
        controller.register_phase_chain(REBASE_RESOLUTION_PHASE, list(RESOLUTION_AGENTS))
        with capture_selection_announcements() as announcements:
            outcome = auto_integrate_after_commit(
                _scenario_config(),
                WorkspaceScope(repo_dir),
                RebaseState(),
                rebase_stop_resolver=runner,
                candidates=RESOLUTION_AGENTS,
                sleep=lambda _seconds: None,
                jitter=lambda: 0.0,
            )

    assert outcome is not None
    assert runner.launches == 0, (
        f"nothing is paused and nothing is unmerged, so no agent may launch, "
        f"got {runner.launches} launches"
    )
    assert not [line for line in announcements if f"Phase {REBASE_RESOLUTION_PHASE}" in line], (
        "the rebase phase must not be announced at all when nothing is paused and "
        f"nothing is unmerged, got {announcements}"
    )
