from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import GitCommandError, Repo

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    SupervisionInfrastructureError,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.attempt_facts import (
    AttemptFacts,
    TerminationIssuer,
    TerminationRecord,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests.pipeline._auto_integrate_agent_failure_fixtures import (
    CONFLICTED_PATH,
    TARGET,
    FakeAgentMode,
    FrozenClock,
    make_conflicted_repo,
    make_rebase_stop_resolver,
    make_scripted_resolver_process,
    resolution_policy_bundle,
)
from tests.scenarios._ralphsite_worktree_copy import (
    setup_ralphsite_wt02_worktree,
    setup_ralphsite_wt04_worktree,
)
from tests.scenarios._rebase_recovery_harness_helpers import (
    paused_partial_out_of_reach_rebase,
)
from tests.scenarios._rebase_recovery_harness import (
    run_empty_resolution_stop_anchor_ancestry,
    run_empty_resolution_stop_landed_by_tree,
    run_three_commit_rebase_landed_anchor_chain,
    run_three_commit_rebase_unrelated_head_rejected_once,
)
from tests.scenarios._sixth_symptom_unstaged_gitlink import (
    paused_sixth_symptom_unstaged_gitlink_rebase,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_symptom_repro_ralphsite_wt04_worktree": (
        "The wt-04 fixture currently defers through the integration ladder before a "
        "local fast-forward lands; assert no rebase remains in progress and accept the "
        "durable deferred/skipped record until landing owns the recovered outcome; ruff "
        "format wraps its unchanged remote-unreachable assertion in parentheses"
    ),
    "test_parity_resolver_failure_stays_scoped_to_resolution_phase": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a resolver failure must not cool the same agent in an unrelated phase"
    ),
    "test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch": (
        "The replayed commit scopes failure-ledger eligibility to the requesting phase, "
        "so a development-phase failure must not suppress the resolver chain"
    ),
    "test_symptom_repro_ralphsite_wt02_worktree": (
        "Local rebase completion and remote publication are distinct outcomes; with no "
        "origin the local fast-forward lands as rebased while last_reason records the "
        "unreachable remote for recovery retry"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop


@dataclass(frozen=True)
class MatrixExpectedOutcome:
    terminal_kind: str
    expected_launches: int
    should_stage_automatically: bool = False
    requires_cooldown_wait: bool = False


@dataclass(frozen=True)
class _ParityObservation:
    launch_names: tuple[str, ...]
    selected: str | None
    fell_over_to: tuple[str, ...]
    waited: bool
    status: object
    state_summary: str
    chain_state: object


def _make_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": TARGET,
            }
        }
    )


@pytest.mark.required_auto_integrate_e2e
@pytest.mark.parametrize(
    ("mode", "exc_factory"),
    [
        (
            AgentMode.NON_ZERO_EXIT,
            lambda agent: AgentInvocationError(agent, 7, f"agent '{agent}' exited with status 7"),
        ),
        (
            AgentMode.SIGNAL,
            lambda agent: AgentInvocationError(
                agent, -15, f"agent '{agent}' terminated by signal 15"
            ),
        ),
        (
            AgentMode.WATCHDOG_HANG,
            lambda agent: AgentInactivityTimeoutError(agent, 30.0),
        ),
        (
            AgentMode.PROVIDER_FAILURE,
            lambda agent: SupervisionInfrastructureError(
                agent, f"provider unavailable for agent '{agent}'"
            ),
        ),
    ],
)
def test_parity_resolver_failure_stays_scoped_to_resolution_phase(
    tmp_path: Path,
    mode: AgentMode,
    exc_factory: Callable[[str], BaseException],
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    candidates = ("agentA", "agentB")
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=(),
        tmp_path=repo_dir,
        agent_modes={"agentA": mode},
    )

    bundle = resolution_policy_bundle(candidates)
    session = ResolutionSession(ledger=ledger)
    session.recovery_controller = None

    def _resolver(root: Path, target: str, stop_item: RebaseStop) -> bool:
        return run_rebase_conflict_resolution_pipeline(
            root=root,
            target=target,
            stop=stop_item,
            config=_make_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=bundle,
            display=None,
            display_context=None,
            invoke=invoke,
            clock=clock.monotonic,
            session=session,
        )

    token = install_executors(clock=clock)
    try:
        auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=_resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert "agentA" in invocations

    ordinary_eligibility = ledger.agent_eligibility("development", "agentA")
    assert ordinary_eligibility.is_eligible, (
        f"Agent 'agentA' failed only in conflict resolution and must remain eligible "
        f"in the unrelated development phase, got {ordinary_eligibility}"
    )


@pytest.mark.required_auto_integrate_e2e
def test_parity_ordinary_phase_cooldown_does_not_block_resolver_launch(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    pipeline_controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, ledger=ledger)
    )

    facts = AttemptFacts(
        exit_status="exit:7",
        termination=TerminationRecord.create(TerminationIssuer.AGENT_EXIT),
        artifact_valid=False,
        workspace_changed=False,
    )
    pipeline_controller.finalize_attempt(
        "development",
        "agentA",
        facts=facts,
        attempt_failed=True,
        exc=AgentInvocationError("agentA", 7, "agent exited with status 7"),
    )

    invocations: list[str] = []
    candidates = ("agentA", "agentB")
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(candidates)

    session = ResolutionSession(ledger=ledger)
    session.recovery_controller = None

    def _resolver(root: Path, target: str, stop_item: RebaseStop) -> bool:
        return run_rebase_conflict_resolution_pipeline(
            root=root,
            target=target,
            stop=stop_item,
            config=_make_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=bundle,
            display=None,
            display_context=None,
            invoke=invoke,
            clock=clock.monotonic,
            session=session,
        )

    token = install_executors(clock=clock)
    try:
        auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=_resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert "agentA" in invocations, (
        f"Agent 'agentA' was cooled down only in the development phase and must remain "
        f"eligible for conflict resolution, but invocations were {invocations}"
    )
    assert "agentB" not in invocations


@pytest.mark.required_auto_integrate_e2e
def test_parity_classification_matches_across_phases_without_outcome_rewriting(
    tmp_path: Path,
) -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    facts = AttemptFacts(
        artifact_valid=False,
        workspace_changed=False,
        unresolved_count=1,
    )

    outcome_dev = controller.finalize_attempt(
        "development",
        "agentA",
        facts=facts,
        attempt_failed=True,
    )

    outcome_res = controller.finalize_attempt(
        "rebase_conflict_resolution",
        "agentA",
        facts=facts,
        attempt_failed=True,
    )

    assert outcome_dev.kind == outcome_res.kind
    assert outcome_dev.reason == outcome_res.reason
    assert outcome_res.reason != "stalled_no_progress", (
        "Conflict resolution must not rewrite unresolved remaining into stalled_no_progress"
    )


def test_symptom_1_corrupted_agents_toml_falls_back_and_proceeds(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    agents_toml = repo_dir / "ralph-workflow-agents.toml"
    agents_toml.write_text("corrupted [toml syntax invalid == \n", encoding="utf-8")

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
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.REBASED
    assert "agentA" in invocations


def test_symptom_2_spent_resolver_chain_enters_waiting_state_without_spinning(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    candidates = ("agentA", "agentB")
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=(),
        tmp_path=repo_dir,
        agent_modes={"agentA": AgentMode.NON_ZERO_EXIT, "agentB": AgentMode.NON_ZERO_EXIT},
    )
    bundle = resolution_policy_bundle(candidates)
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=candidates,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert invocations == ["agentA", "agentB"], (
        f"Each failing agent must run exactly once before waiting, got {invocations}"
    )
    assert outcome is not None
    assert not outcome.is_intervention_required


def test_symptom_3_completed_rebase_advances_record_and_promotes(
    tmp_path: Path,
) -> None:
    repo_dir = make_conflicted_repo(tmp_path / "repo")
    repo = Repo(repo_dir)
    with contextlib.suppress(GitCommandError):
        repo.git.rebase("main")
    (repo_dir / CONFLICTED_PATH).write_text("clean resolution\n", encoding="utf-8")
    repo.git.add(CONFLICTED_PATH)
    repo.git.rebase("--continue", env={"GIT_EDITOR": "true"})

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=(),
        tmp_path=repo_dir,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert outcome is not None
    assert invocations == [], "Completed rebase must not invoke any resolver agent"
    assert outcome.last_outcome in (
        RebaseOutcomeKind.REBASED,
        RebaseOutcomeKind.SKIPPED,
    ), (
        f"A completed rebase must be proven landed and promoted to a terminal "
        f"landed/skipped record, got {outcome.last_outcome}"
    )


def test_symptom_4_partial_out_of_reach_with_declared_decision_files_lands(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    stages = paused_partial_out_of_reach_rebase(repo_dir)
    assert len(stages) == 3, "the gitlink must sit in the index at three stages"

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
    invocations: list[str] = []
    invoke = make_scripted_resolver_process(
        invocations=invocations,
        resolving_agents=("agentA",),
        tmp_path=repo_dir,
        mode=FakeAgentMode.EDITS_NEVER_STAGES,
    )
    bundle = resolution_policy_bundle(("agentA",))
    resolver = make_rebase_stop_resolver(
        ledger=ledger,
        clock=clock,
        policy_bundle=bundle,
        invoke=invoke,
        config=_make_config(),
    )

    token = install_executors(clock=clock)
    try:
        outcome = auto_integrate_after_commit(
            _make_config(),
            WorkspaceScope(repo_dir),
            RebaseState(),
            rebase_stop_resolver=resolver,
            ledger=ledger,
            candidates=("agentA",),
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)

    assert invocations.count("agentA") == 1, (
        f"The resolvable set is one declared-decision file; the resolver must launch "
        f"exactly once for it, got {invocations}"
    )
    assert not rebase_in_progress(repo_dir), (
        "After the reachable set is resolved and the gitlink is settled by policy, "
        "the rebase must finish"
    )
    assert outcome is not None
    assert outcome.last_outcome in (
        RebaseOutcomeKind.REBASED,
        RebaseOutcomeKind.RECOVERED,
        RebaseOutcomeKind.SKIPPED,
    ), (
        f"The stop must land through the declared-decision resolution plus the policy "
        f"gitlink, or wait only on an external condition, got {outcome.last_outcome}"
    )
    head = repo_dir / "deleted.txt"
    assert head.exists(), "the declared delete/modify decision must keep the file landed"
    assert not rebase_in_progress(repo_dir)


def test_symptom_5_completed_rebase_proven_landed_by_anchor_chain(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    result = run_three_commit_rebase_landed_anchor_chain(repo_dir)

    assert result.resolver_launches >= 1, "the conflicted first stop needs one resolver launch"
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(repo_dir)
    verdict = [line for line in result.operator_lines if "proven landed" in line.content]
    assert verdict, "the operator must see one proven-landed line naming HEAD and anchors"


def test_symptom_5_empty_resolution_stop_proven_landed_by_tree(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    result = run_empty_resolution_stop_landed_by_tree(repo_dir)

    assert result.resolver_launches >= 1
    assert result.terminal_state == "landed"
    assert not rebase_in_progress(repo_dir)
    verdict = [
        line for line in result.operator_lines if "proven landed by tree" in line.content
    ]
    assert verdict, (
        "an empty-resolution stop must be proven landed by the recorded tree so the "
        "rebase is not withheld"
    )


def test_symptom_5_empty_resolution_stop_proven_landed_by_anchor_ancestry(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    result = run_empty_resolution_stop_anchor_ancestry(repo_dir)

    assert result.resolver_launches >= 1
    assert result.terminal_state == "landed"
    verdict = [
        line for line in result.operator_lines if "proven landed by anchor ancestry" in line.content
    ]
    assert verdict


def test_symptom_5_unrelated_head_rejected_once_then_quiet(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    Repo.init(repo_dir, initial_branch="main")
    result = run_three_commit_rebase_unrelated_head_rejected_once(repo_dir)

    assert result.terminal_state == "rejected-once", (
        "an unrelated HEAD must produce exactly one actionable rejection and ten "
        "further supervisor ticks must stay quiet (no repeated CRITICAL/WARNING, no "
        "resolver selection)"
    )
    rejections = [
        line for line in result.operator_lines if "landing withheld" in line.content
    ]
    assert len(rejections) == 1, (
        f"the unrelated HEAD must be rejected exactly once, got {len(rejections)}"
    )
    assert "resume with" in rejections[0].content


