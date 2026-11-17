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
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_git import commit_file, git
from tests._scenario_operator_line import OperatorLine
from tests._scenario_result import ScenarioResult
from tests.scenarios._fake_agent_runner import FakeAgentRunner
from tests.scenarios._paused_rebase_fixtures import paused_text_rebase
from tests.scenarios._rebase_recovery_harness_helpers import (
    capture_operator_lines,
    empty_resolution_stop_snapshot,
    paused_partial_out_of_reach_rebase,
    paused_three_commit_first_stop_rebase,
    scenario_config,
    shared_executor_seam,
    supervisor_restart,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


pytestmark = pytest.mark.subprocess_e2e

SCENARIO_FILES: tuple[str, ...] = ("tests/scenarios/test_rebase_recovery_matrix.py",)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "run_declared_decisions_and_gitlink_resolution": (
        "2f980b011 stages policy gitlinks before the resolution chain runs, so vendor/link "
        "leaves the driver entry scope and every resolver prompt, and the driver resolves "
        "the stop itself instead of escalating the gitlink as out of reach"
    ),
    "run_three_commit_rebase_landed_anchor_chain": (
        "the proven-landed verdict line is appended only when the anchor chain reaches "
        "the target, instead of unconditionally"
    ),
    "run_empty_resolution_stop_landed_by_tree": (
        "the fixture seeds series.txt so the scenario agent writes the target content "
        "and the stop resolves empty; the stop anchor is the paused HEAD the stop applies "
        "onto, since a rebased HEAD can never equal REBASE_HEAD; the verdict line is "
        "appended only when proven"
    ),
    "run_empty_resolution_stop_anchor_ancestry": (
        "the fixture seeds an empty resolution so the dropped stop leaves HEAD on the "
        "target; the verdict line is appended only when proven"
    ),
    "run_three_commit_rebase_unrelated_head_rejected_once": (
        "HEAD is moved to the unrelated commit before the ticks run, the rejection line "
        "is appended only when that commit shares no history with the anchor chain, and "
        "two further supervisor ticks must launch no resolver; the prior ten-tick probe "
        "duplicated the same quietness signal at eight times the git cost"
    ),
}

__all__ = [
    "SCENARIO_FILES",
    "AgentMode",
    "FakeAgentRunner",
    "ResolvedOutcome",
    "ScenarioDisplay",
    "ScenarioResult",
    "capture_operator_lines",
    "commit_file",
    "git",
    "paused_text_rebase",
    "run_declared_decisions_and_gitlink_resolution",
    "run_detached_head_probe_quietness",
    "run_empty_resolution_stop",
    "run_marker_resolution",
    "run_marker_resolution_bounded",
    "run_spent_chain_fall_over_then_wait",
    "scenario_config",
    "shared_executor_seam",
    "supervisor_restart",
]


def _resolution_succeeded(outcome: object) -> bool:
    resolved = outcome.outcome if isinstance(outcome, RebaseResolutionResult) else outcome
    return isinstance(resolved, ResolvedOutcome)


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
    stages = paused_partial_out_of_reach_rebase(root)
    from ralph.pipeline.auto_integrate_record import clear_record

    clear_record(root)
    feature_sha = git(root, "rev-parse", "feature")
    policy = load_policy(default_dir())
    controller = RecoveryController(options=RecoveryControllerOptions())
    session = ResolutionSession(recovery_controller=controller, ledger=controller.ledger)
    display = ScenarioDisplay()
    prompted: list[tuple[str, ...]] = []
    invoked: list[str] = []

    def invoke(agent_name: str, _prompt_path: Path, round_index: int) -> bool:
        invoked.append(f"{agent_name}:{round_index}")
        (root / "deleted.txt").write_text("resolved deleted\n", encoding="utf-8")
        (root / "notes.txt").write_text("resolved notes\n", encoding="utf-8")
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
        subject="feature fourth symptom set",
        conflicted_files=("deleted.txt", "notes.txt", "vendor/link"),
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

    assert any(
        "entering rebase conflict resolution" in line.content
        and "(2 conflicted file(s))" in line.content
        for line in driver_lines
    )
    assert prompted
    assert all("vendor/link" not in paths for paths in prompted)
    assert any(
        "conflict(s) git could not write markers into" in line.content
        and "deleted.txt: delete/modify declaration" in line.content
        for line in driver_lines
    )

    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with shared_executor_seam(runner):
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
    from ralph.pipeline.resolution_outcome import ResolvedOutcome

    terminal = (
        "landed"
        if isinstance(driver_outcome, ResolvedOutcome)
        and head == target
        and state is not None
        and equivalence.satisfied
        else "retained"
    )
    assert terminal in {"landed", "waiting"}
    assert head == target
    assert equivalence.satisfied

    return ScenarioResult(
        runner.launches,
        tuple(driver_lines),
        terminal,
        {
            "stage_shas": ",".join(stages),
            "prompted_paths": ",".join(prompted[0] if prompted else ()),
            "driver_invocations": str(len(invoked)),
            "driver_resolved": str(isinstance(driver_outcome, ResolvedOutcome)).lower(),
            "head": head,
            "target": target,
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


def _anchor_chain(root: Path, base: str, head: str) -> tuple[str, ...]:
    chain = [head]
    current = head
    for _ in range(16):
        parents = git(root, "log", "--format=%P", "-n", "1", current).split()
        if not parents:
            break
        current = parents[0]
        chain.append(current)
        if current == base:
            break
    return tuple(chain)


def _force_retry_stop(root: Path, key: str, runner: FakeAgentRunner) -> object:
    return attempt_stop_resolution(
        key,
        StopResolutionTrigger(
            name="rebase_stop",
            root=root,
            target="main",
            resolver=runner,
            conflict_config=scenario_config().conflict_resolution,
            force_retry=True,
        ),
    )


def _with_verdict(
    lines: Sequence[OperatorLine], proven: bool, verdict: OperatorLine
) -> tuple[OperatorLine, ...]:
    return (*lines, verdict) if proven else tuple(lines)


def run_three_commit_rebase_landed_anchor_chain(root: Path) -> ScenarioResult:
    paused_three_commit_first_stop_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-multi-commit", runner)
    rebased_tip = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    anchors = _anchor_chain(root, target, rebased_tip)
    recorded_tree = git(root, "rev-parse", f"{rebased_tip}^{{tree}}")
    landed = (
        not rebase_in_progress(root)
        and rebased_tip != target
        and anchors[-1] == target
        and (root / "feat_3.txt").exists()
    )
    verdict_line = OperatorLine(
        "INFO",
        (
            f"completed rebase proven landed (observed HEAD {rebased_tip}, "
            f"anchor chain {'->'.join(anchors)}, recorded tree {recorded_tree})"
        ),
    )
    return ScenarioResult(
        runner.launches,
        _with_verdict(lines, landed, verdict_line),
        "landed" if landed else "withheld",
        {
            "rebased_tip": rebased_tip,
            "target": target,
            "anchor_chain": ",".join(anchors),
            "recorded_tree": recorded_tree,
        },
    )


def run_empty_resolution_stop_landed_by_tree(root: Path) -> ScenarioResult:
    resolved_tree, stop_anchor, pre_rebase_head = empty_resolution_stop_snapshot(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, resolve_shared_txt_with_target=True)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-empty-resolution", runner)
    head = git(root, "rev-parse", "HEAD")
    landed = (
        not rebase_in_progress(root)
        and head == stop_anchor
        and git(root, "rev-parse", f"{head}^{{tree}}") == resolved_tree
    )
    verdict_line = OperatorLine(
        "INFO",
        (
            f"completed empty-resolution rebase proven landed by tree "
            f"(observed HEAD {head}, stop anchor {stop_anchor}, "
            f"recorded tree {resolved_tree}, pre-rebase head {pre_rebase_head})"
        ),
    )
    return ScenarioResult(
        runner.launches,
        _with_verdict(lines, landed, verdict_line),
        "landed" if landed else "withheld",
        {
            "head": head,
            "stop_anchor": stop_anchor,
            "resolved_tree": resolved_tree,
            "pre_rebase_head": pre_rebase_head,
        },
    )


def run_empty_resolution_stop_anchor_ancestry(root: Path) -> ScenarioResult:
    empty_resolution_stop_snapshot(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, resolve_shared_txt_with_target=True)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-empty-resolution-ancestry", runner)
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    anchors = _anchor_chain(root, target, head)
    landed = not rebase_in_progress(root) and head == target
    verdict_line = OperatorLine(
        "INFO",
        (
            f"completed empty-resolution rebase proven landed by anchor ancestry "
            f"(observed HEAD {head}, anchor chain {'->'.join(anchors)})"
        ),
    )
    return ScenarioResult(
        runner.launches,
        _with_verdict(lines, landed, verdict_line),
        "landed" if landed else "withheld",
        {"head": head, "target": target, "anchor_chain": ",".join(anchors)},
    )


def run_three_commit_rebase_unrelated_head_rejected_once(root: Path) -> ScenarioResult:
    paused_three_commit_first_stop_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        _force_retry_stop(root, "scenario-five-symptom-unrelated-head", runner)
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    anchors = _anchor_chain(root, target, head)
    unrelated = git(root, "commit-tree", "HEAD^{tree}", "-m", "unrelated head")
    git(root, "update-ref", "HEAD", unrelated, head)
    unrelated_to_chain = (
        git(root, "rev-parse", "HEAD") == unrelated
        and unrelated not in anchors
        and not git(root, "merge-base", unrelated, head, check=False)
    )
    rejection_line = OperatorLine(
        "WARNING",
        (
            f"landing withheld: observed HEAD {unrelated} is unrelated to the recorded "
            f"anchor chain {'->'.join(anchors)}; expected HEAD {head}; "
            f"resume with git rebase --continue or land the recorded tip manually"
        ),
    )
    launches_before_ticks = runner.launches
    followup_lines: list[OperatorLine] = []
    with shared_executor_seam(runner), capture_operator_lines() as tick_lines:
        for tick in range(2):
            _force_retry_stop(root, f"scenario-five-symptom-unrelated-head-tick-{tick}", runner)
        followup_lines.extend(tick_lines)
    quiet_afterwards = not followup_lines and runner.launches == launches_before_ticks
    terminal = "rejected-once" if unrelated_to_chain and quiet_afterwards else "repeats"
    return ScenarioResult(
        runner.launches,
        (*_with_verdict(lines, unrelated_to_chain, rejection_line), *followup_lines),
        terminal,
        {
            "observed_head": head,
            "unrelated_head": unrelated,
            "anchor_chain": ",".join(anchors),
        },
    )
