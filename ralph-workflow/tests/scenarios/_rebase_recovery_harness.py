from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_git import commit_file, git
from tests._scenario_operator_line import OperatorLine
from tests._scenario_result import ScenarioResult
from tests.scenarios._rebase_recovery_harness_helpers import (
    FakeAgentRunner,
    _capture_log_records,
    capture_operator_lines,
    corrupted_config,
    paused_declared_decisions_and_gitlink_rebase,
    paused_multi_commit_empty_stop_rebase,
    paused_multi_commit_first_stop_rebase,
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
    scenario_config,
    shared_executor_seam,
    supervisor_restart,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.agents.timeout_clock import FakeClock

pytestmark = pytest.mark.subprocess_e2e

ORDINARY_PHASE_EXECUTOR_SEAM_MODULE: str = "ralph.pipeline.effect_executor"
ORDINARY_PHASE_EXECUTOR_SEAM_CALLABLE: str = "execute_agent_effect"

SCENARIO_FILES: tuple[str, ...] = ("tests/scenarios/test_rebase_recovery_matrix.py",)

__all__ = [
    "ORDINARY_PHASE_EXECUTOR_SEAM_CALLABLE",
    "ORDINARY_PHASE_EXECUTOR_SEAM_MODULE",
    "SCENARIO_FILES",
    "AgentMode",
    "FakeAgentRunner",
    "OperatorLine",
    "ResolvedOutcome",
    "RetryLaterOutcome",
    "ScenarioDisplay",
    "ScenarioResult",
    "StopResolutionOutcome",
    "capture_operator_lines",
    "commit_file",
    "corrupted_config",
    "git",
    "paused_declared_decisions_and_gitlink_rebase",
    "paused_multi_commit_empty_stop_rebase",
    "paused_multi_commit_first_stop_rebase",
    "paused_text_and_gitlink_rebase",
    "paused_text_rebase",
    "run_completed_rebase_promotion",
    "run_corrupted_config",
    "run_declared_decisions_and_gitlink_resolution",
    "run_detached_head_probe_quietness",
    "run_empty_resolution_stop",
    "run_gate_quiescence_ticks",
    "run_marker_resolution",
    "run_marker_resolution_bounded",
    "run_multi_commit_empty_stop_recovery",
    "run_sixth_symptom_landing",
    "run_spent_chain_fall_over_then_wait",
    "run_spent_gate",
    "run_unstaged_text_resolution",
    "scenario_config",
    "shared_executor_seam",
    "supervisor_restart",
]


def run_corrupted_config(root: Path) -> ScenarioResult:
    from ralph.workspace.context import WorkspaceContextError, workspace_context

    paused_text_rebase(root)
    config_path = corrupted_config(root)
    with capture_operator_lines() as lines:
        with pytest.raises(WorkspaceContextError), workspace_context(root):
            pass
        config_path.write_text(
            '[general]\nauto_integrate_enabled = true\nauto_integrate_target = "main"\n',
            encoding="utf-8",
        )
        runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
        with shared_executor_seam(runner):
            outcome = attempt_stop_resolution(
                "scenario-corrupted-config-repaired",
                StopResolutionTrigger(
                    name="rebase_stop",
                    root=root,
                    target="main",
                    resolver=runner,
                    conflict_config=scenario_config().conflict_resolution,
                    force_retry=True,
                ),
            )
            eff_outcome = (
                outcome.outcome if isinstance(outcome, RebaseResolutionResult) else outcome
            )
            succeeded = isinstance(eff_outcome, ResolvedOutcome)
    terminal = "resolved" if succeeded and not rebase_in_progress(root) else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal, {"config": str(config_path)})


def run_completed_rebase_promotion(root: Path) -> ScenarioResult:
    paused_multi_commit_first_stop_rebase(root, commit_count=3)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        state = integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    all_preserved = (root / "feat_2.txt").exists() and (root / "feat_3.txt").exists()
    terminal = (
        "promoted"
        if head == target and state is not None and state.fast_forwarded and all_preserved
        else "orphaned"
    )
    return ScenarioResult(runner.launches, tuple(lines), terminal, {"head": head, "target": target})


def _resolution_succeeded(outcome: object) -> bool:
    resolved = outcome.outcome if isinstance(outcome, RebaseResolutionResult) else outcome
    return isinstance(resolved, ResolvedOutcome)


def run_unstaged_text_resolution(root: Path) -> ScenarioResult:
    paused_text_and_gitlink_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        outcome = attempt_stop_resolution(
            "scenario-unstaged-gitlink",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=runner,
                conflict_config=scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )
        succeeded = _resolution_succeeded(outcome)
    terminal = "resolved" if succeeded and not rebase_in_progress(root) else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_sixth_symptom_landing(root: Path) -> ScenarioResult:
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.integration_owner import load_integration_attempts
    from tests.scenarios._content_equivalence import assert_content_equivalence

    target_before = git(root, "rev-parse", "main")
    stages = paused_text_and_gitlink_rebase(root)
    feature_sha = git(root, "rev-parse", "feature")
    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with (
        shared_executor_seam(runner),
        capture_operator_lines() as lines,
        _capture_log_records() as logs,
    ):
        outcome = attempt_stop_resolution(
            "scenario-sixth-symptom",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=runner,
                conflict_config=scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )
        completed_head = git(root, "rev-parse", "HEAD")
        completed_record = read_record(root)
        state = integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
        launches_after_landing = runner.launches
        selections_after_landing_start = len(
            [record for record in logs if "preferred_agent_index" in record[0]]
        )
        quiet_lines: list[OperatorLine] = []
        for _ in range(10):
            with capture_operator_lines() as tick_lines:
                integrate_before_planning(
                    scenario_config(),
                    WorkspaceScope(root),
                    RebaseState(),
                    conflict_resolver=lambda _root, _target: False,
                    rebase_stop_resolver=runner,
                )
            quiet_lines.extend(tick_lines)
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    equivalence = assert_content_equivalence(
        root,
        contract="the sixth-symptom recovery must preserve feature and target content",
        feature=feature_sha,
        source_refs=(target_before,),
        excluded_paths=("vendor/link",),
    )
    policy_lines = [
        content for _name, content in logs if "gitlink policy path=vendor/link" in content
    ]
    post_landing_selections = [record for record in logs if "preferred_agent_index" in record[0]][
        selections_after_landing_start:
    ]
    attempts = load_integration_attempts(root)
    repeated_pairs = [
        f"{signature}|{action}:{count}"
        for (signature, action), count in attempts.repeat_counts.items()
        if count > attempts.repeat_threshold
    ]
    terminal = (
        "landed"
        if _resolution_succeeded(outcome)
        and head == target
        and state is not None
        and equivalence.satisfied
        else "retained"
    )
    return ScenarioResult(
        runner.launches,
        tuple(lines),
        terminal,
        {
            "stage_shas": ",".join(stages),
            "policy_lines": "\n".join(policy_lines),
            "completed_head": completed_head,
            "integrated_feature_sha": (
                ""
                if completed_record is None
                else str(completed_record.integrated_feature_sha or "")
            ),
            "head": head,
            "target": target,
            "launches_after_landing": str(runner.launches - launches_after_landing),
            "lines_after_landing": str(len(quiet_lines)),
            "post_landing_selections": str(len(post_landing_selections)),
            "repeated_pairs": "\n".join(repeated_pairs),
        },
    )


def run_declared_decisions_and_gitlink_resolution(root: Path) -> ScenarioResult:
    from ralph.display.context import make_display_context
    from ralph.pipeline.conflict_resolution import (
        RebaseStop,
        run_rebase_conflict_resolution_outcome,
    )
    from ralph.pipeline.conflict_resolution.prompt import materialize_round_prompt
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.loader import default_dir, load_policy
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
    from tests.scenarios._content_equivalence import assert_content_equivalence

    target_before = git(root, "rev-parse", "main")
    stages = paused_declared_decisions_and_gitlink_rebase(root)
    feature_sha = git(root, "rev-parse", "feature")
    policy = load_policy(default_dir())
    controller = RecoveryController(options=RecoveryControllerOptions())
    session = ResolutionSession(recovery_controller=controller, ledger=controller.ledger)
    display = ScenarioDisplay()
    prompted: list[tuple[str, ...]] = []
    invoked: list[str] = []

    def invoke(agent_name: str, _prompt_path: Path, round_index: int) -> bool:
        invoked.append(f"{agent_name}:{round_index}")
        return True

    def materializer(
        *,
        root: Path,
        target: str,
        conflicted_paths: Sequence[str],
        round_index: int,
        round_cap: int,
        session: ResolutionSession,
        stop: RebaseStop | None,
        strategy_history: tuple[str, ...],
    ) -> Path | None:
        prompted.append(tuple(str(path) for path in conflicted_paths))
        return materialize_round_prompt(
            root=root,
            target=target,
            conflicted_paths=conflicted_paths,
            round_index=round_index,
            round_cap=round_cap,
            session=session,
            stop=stop,
            strategy_history=strategy_history,
        )

    stop = RebaseStop(
        sha=git(root, "rev-parse", "REBASE_HEAD"),
        subject="feature declared set",
        conflicted_files=("deleted.txt", "docs/decision.md", "vendor/link"),
        stop_index=1,
        stop_cap=4,
    )
    deps = PipelineDeps(
        display_context=make_display_context(),
        recovery_controller=controller,
        policy_bundle=policy,
    )
    with capture_operator_lines(display) as driver_lines:
        driver_outcome = run_rebase_conflict_resolution_outcome(
            root=root,
            target="main",
            stop=stop,
            config=scenario_config(),
            pipeline_deps=deps,
            workspace_scope=WorkspaceScope(root),
            policy_bundle=policy,
            display=display,
            display_context=None,
            invoke=invoke,
            session=session,
            prompt_materializer=materializer,
        )
    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with (
        shared_executor_seam(runner),
        capture_operator_lines() as landing_lines,
        _capture_log_records() as logs,
    ):
        state = integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    equivalence = assert_content_equivalence(
        root,
        contract="mixed declared-decision and policy paths must land without content loss",
        feature=feature_sha,
        source_refs=(target_before,),
        excluded_paths=("vendor/link",),
    )
    policy_lines = [
        content for _name, content in logs if "gitlink policy path=vendor/link" in content
    ]
    all_lines = (*driver_lines, *landing_lines)
    from ralph.pipeline.resolution_outcome import ResolvedOutcome

    terminal = (
        "landed"
        if not isinstance(driver_outcome, ResolvedOutcome)
        and head == target
        and state is not None
        and equivalence.satisfied
        else "retained"
    )
    return ScenarioResult(
        runner.launches,
        all_lines,
        terminal,
        {
            "stage_shas": ",".join(stages),
            "policy_lines": "\n".join(policy_lines),
            "prompted_paths": ",".join(prompted[0] if prompted else ()),
            "driver_invocations": str(len(invoked)),
            "driver_resolved": str(isinstance(driver_outcome, ResolvedOutcome)).lower(),
            "head": head,
            "target": target,
        },
    )


def run_gate_quiescence_ticks(root: Path) -> ScenarioResult:
    from ralph.agents.timeout_clock import FakeClock

    clock = FakeClock(start=1000.0)
    runner = FakeAgentRunner(AgentMode.PROVIDER_FAILURE, clock=clock)

    class _IntegrationClock:
        def now(self) -> float:
            return clock.monotonic()

        def monotonic(self) -> float:
            return clock.monotonic()

        def epoch(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    paused_text_rebase(root)
    token = install_executors(agent_runner=runner, clock=_IntegrationClock())
    all_lines: list[OperatorLine] = []
    try:
        with shared_executor_seam(runner):
            with capture_operator_lines() as first:
                integrate_before_planning(
                    scenario_config(),
                    WorkspaceScope(root),
                    RebaseState(),
                    conflict_resolver=lambda _root, _target: False,
                    rebase_stop_resolver=runner,
                )
            all_lines.extend(first)
            launches_before_gate = runner.launches
            for _ in range(11):
                clock.advance(1.0)
                with capture_operator_lines() as tick:
                    integrate_before_planning(
                        scenario_config(),
                        WorkspaceScope(root),
                        RebaseState(),
                        conflict_resolver=lambda _root, _target: False,
                        rebase_stop_resolver=runner,
                    )
                all_lines.extend(tick)
    finally:
        reset_executors(token)
    waiting = [line.content for line in all_lines if "waiting:" in line.content.lower()]
    gate = [line.content for line in all_lines if "gate active" in line.content.lower()]
    return ScenarioResult(
        runner.launches - launches_before_gate,
        tuple(all_lines),
        "quiescent" if rebase_in_progress(root) else "resolved",
        {
            "waiting_lines": "\n".join(waiting),
            "waiting_count": str(len(waiting)),
            "gate_lines": "\n".join(gate),
            "gate_count": str(len(gate)),
        },
    )


def run_marker_resolution(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.LEAVES_MARKER)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        outcome = attempt_stop_resolution(
            "scenario-marker",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=runner,
                conflict_config=scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )
        succeeded = _resolution_succeeded(outcome)
    terminal = "resolved" if succeeded else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_spent_gate(root: Path, clock: FakeClock, *, advance: float = 0.0) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.PROVIDER_FAILURE, clock=clock)
    if advance:
        clock.advance(advance)

    class _IntegrationClock:
        def now(self) -> float:
            return clock.monotonic()

        def monotonic(self) -> float:
            return clock.monotonic()

        def epoch(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(agent_runner=runner, clock=_IntegrationClock())
    try:
        with shared_executor_seam(runner), capture_operator_lines() as lines:
            state = integrate_before_planning(
                scenario_config(),
                WorkspaceScope(root),
                RebaseState(),
                conflict_resolver=lambda _root, _target: False,
                rebase_stop_resolver=runner,
            )
    finally:
        reset_executors(token)
    terminal = (
        "waiting"
        if rebase_in_progress(root)
        or (state is not None and getattr(state, "last_decision", None) == "block_for_recovery")
        else "resolved"
    )
    facts = {
        "launch_times": ",".join(str(item) for item in runner.launch_times),
        "next_launch_time": str(getattr(state, "retry_not_before", "")),
    }
    return ScenarioResult(runner.launches, tuple(lines), terminal, facts)


def run_spent_chain_fall_over_then_wait(root: Path) -> ScenarioResult:
    from ralph.agents.timeout_clock import FakeClock

    paused_text_rebase(root)
    clock = FakeClock(start=1000.0)
    runner = FakeAgentRunner(AgentMode.PROVIDER_FAILURE, clock=clock)

    class _IntegrationClock:
        def now(self) -> float:
            return clock.monotonic()

        def monotonic(self) -> float:
            return clock.monotonic()

        def epoch(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(agent_runner=runner, clock=_IntegrationClock())
    try:
        with shared_executor_seam(runner), capture_operator_lines():
            integrate_before_planning(
                scenario_config(),
                WorkspaceScope(root),
                RebaseState(),
                conflict_resolver=lambda _root, _target: False,
                rebase_stop_resolver=runner,
            )
        launches_after_first = runner.launches
        clock.advance(1.0)
        with shared_executor_seam(runner), capture_operator_lines() as second_lines:
            state2 = integrate_before_planning(
                scenario_config(),
                WorkspaceScope(root),
                RebaseState(),
                conflict_resolver=lambda _root, _target: False,
                rebase_stop_resolver=runner,
            )
        second_launches = runner.launches - launches_after_first
    finally:
        reset_executors(token)

    waiting_lines = [
        line
        for line in second_lines
        if "provider" in line.content.lower() or "waiting" in line.content.lower()
    ]
    terminal = (
        "waiting"
        if rebase_in_progress(root)
        or (state2 is not None and getattr(state2, "last_decision", None) == "block_for_recovery")
        else "resolved"
    )
    return ScenarioResult(
        second_launches,
        tuple(waiting_lines or second_lines[:1]),
        terminal,
        {"next_launch_time": str(getattr(state2, "retry_not_before", ""))},
    )


def run_empty_resolution_stop(root: Path) -> ScenarioResult:
    commit_file(root, "shared.txt", "base\n", "base")
    commit_file(root, "series.txt", "seed\n", "seed")
    git(root, "checkout", "-q", "-b", "feature")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target value\n", "target change")
    git(root, "checkout", "-q", "feature")
    commit_file(root, "shared.txt", "feature diff\n", "feature stop that will resolve empty")
    commit_file(root, "series.txt", "seed\nsecond\n", "feature second commit")
    git(root, "rebase", "main", check=False)

    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    terminal = (
        "resolved"
        if not rebase_in_progress(root)
        and head == target
        and (root / "series.txt").read_text(encoding="utf-8") == "seed\nsecond\n"
        else "retained"
    )
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_detached_head_probe_quietness(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    from ralph.pipeline.auto_integrate_context import current_branch_or_detached_marker

    with capture_operator_lines() as lines:
        marker = current_branch_or_detached_marker(root)
        restarted = supervisor_restart(root)
    quiet = len(lines) == 0 and restarted.get("status") is not None
    terminal = "quiet" if quiet else "noisy"
    return ScenarioResult(0, tuple(lines), terminal, {"marker": str(marker)})


def run_marker_resolution_bounded(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.LEAVES_MARKER)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        for _ in range(2):
            attempt_stop_resolution(
                "scenario-marker-bounded",
                StopResolutionTrigger(
                    name="rebase_stop",
                    root=root,
                    target="main",
                    resolver=runner,
                    conflict_config=scenario_config().conflict_resolution,
                    force_retry=True,
                ),
            )
            if not rebase_in_progress(root):
                break
    bounded = runner.launches <= 2
    terminal = "retained" if bounded and rebase_in_progress(root) else "unbounded"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_multi_commit_empty_stop_recovery(root: Path) -> ScenarioResult:
    paused_multi_commit_empty_stop_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        state = integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    restarted = supervisor_restart(root)
    terminal = (
        "anchor_proven"
        if restarted["head"] == target and state is not None and not rebase_in_progress(root)
        else "anchor_lost"
    )
    return ScenarioResult(
        runner.launches,
        tuple(lines),
        terminal,
        {"head": head, "target": target, "restart_head": str(restarted["head"])},
    )
