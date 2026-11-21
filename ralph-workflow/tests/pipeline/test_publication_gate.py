from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.phases.required_artifacts import retry_hint_path
from ralph.pipeline.publication_gate import (
    MAX_INCONCLUSIVE_GATE_RUNS,
    MAX_PUBLICATION_GATE_FAILURES,
    PublicationBlock,
    PublicationGateFailed,
    PublicationGateRun,
    blocked_publication,
    check_publication_gate,
    declared_publication_gate,
    leave_publication_gate_feedback,
    publication_gate_feedback,
    publication_gate_loop_phase,
    publication_gate_record_path,
)
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle

if TYPE_CHECKING:
    from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_repeated_gate_failures_stop_routing_back_once_the_budget_is_spent": (
        "operator directive: the route-back cap counts only route-backs that actually gave "
        "development the feedback; consecutive_failures is diagnostic, so a sixth red "
        "evaluation with no route-back still routes back (wt-05 sat at 12 failures and 0 "
        "route-backs with its agents never told why the gate failed)"
    ),
    "test_no_feedback_is_routed_once_the_gate_failure_budget_is_spent": (
        "replaced by test_red_evaluations_from_non_commit_landings_never_starve_the_commit_"
        "feedback: red evaluations from non-commit landings no longer exhaust the feedback "
        "budget; the route-back bound itself stays pinned by "
        "test_route_backs_on_an_unchanged_tree_are_bounded"
    ),
    "test_a_result_is_reused_only_for_the_same_tree": (
        "reusing the red result for an unchanged tree no longer counts as another gate "
        "failure; the counter counts real evaluations, and route-backs are bounded separately"
    ),
}

_FACT = (
    "RALPH-FACT: authoritative_verify_command: `make -C ralph-workflow verify` "
    "(runs the fail-closed prerequisites, then the verify steps)."
)
_TREE_A = "a" * 40
_TREE_B = "b" * 40


def _declare_gate(root: Path, fact_line: str = _FACT) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(f"# Verification policy\n\n{fact_line}\n", encoding="utf-8")


class _ScriptedGate:
    def __init__(self, exit_code: int, output: str = "", *, timed_out: bool = False) -> None:
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out
        self.calls: list[tuple[str, Path, float]] = []

    def __call__(self, command: str, cwd: Path, timeout_seconds: float) -> PublicationGateRun:
        self.calls.append((command, cwd, timeout_seconds))
        return PublicationGateRun(
            exit_code=self.exit_code, output=self.output, timed_out=self.timed_out
        )


def _trees(*shas: str) -> object:
    remaining = list(shas)

    def tree_of(_root: Path, _commit: str) -> str:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return tree_of


def test_the_declared_verify_command_is_the_publication_gate(tmp_path: Path) -> None:
    _declare_gate(tmp_path)

    assert declared_publication_gate(tmp_path) == "make -C ralph-workflow verify"


@pytest.mark.parametrize(
    "fact_line",
    [None, "RALPH-FACT: authoritative_verify_command: PROJECT-FACT-UNRESOLVED"],
)
def test_a_project_without_a_resolved_gate_declares_none(
    tmp_path: Path, fact_line: str | None
) -> None:
    if fact_line is not None:
        _declare_gate(tmp_path, fact_line)

    assert declared_publication_gate(tmp_path) is None
    assert check_publication_gate(tmp_path, "HEAD", runner=_ScriptedGate(1)) is None


def test_a_green_gate_passes_and_runs_in_the_run_worktree(tmp_path: Path) -> None:
    _declare_gate(tmp_path)
    gate = _ScriptedGate(0, "all checks passed\n")

    result = check_publication_gate(
        tmp_path, "HEAD", runner=gate, timeout_seconds=60.0, tree_of=_trees(_TREE_A)
    )

    assert result is not None
    assert result.passed is True
    assert result.block is None
    assert result.tree_sha == _TREE_A
    assert gate.calls == [("make -C ralph-workflow verify", tmp_path, 60.0)]


def test_a_red_gate_blocks_with_its_output_as_feedback(tmp_path: Path) -> None:
    _declare_gate(tmp_path)
    output = "ruff format --check failed\nwould reformat ralph/pipeline/runner.py\n"

    result = check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(2, output), tree_of=_trees(_TREE_A)
    )

    assert result is not None
    assert result.passed is False
    assert result.block is PublicationBlock.GATE_FAILED
    assert "would reformat ralph/pipeline/runner.py" in result.feedback()
    assert "exit 2" in result.feedback()


def test_a_timeout_counts_as_a_failed_gate(tmp_path: Path) -> None:
    _declare_gate(tmp_path)

    result = check_publication_gate(
        tmp_path,
        "HEAD",
        runner=_ScriptedGate(124, "still running", timed_out=True),
        tree_of=_trees(_TREE_A),
    )

    assert result is not None
    assert result.passed is False
    assert result.block is PublicationBlock.GATE_TIMED_OUT


def test_a_real_command_that_outlives_the_timeout_fails_the_gate(tmp_path: Path) -> None:
    _declare_gate(tmp_path, "RALPH-FACT: authoritative_verify_command: `sleep 30`")

    result = check_publication_gate(tmp_path, "HEAD", timeout_seconds=0.2, tree_of=_trees(_TREE_A))

    assert result is not None
    assert result.timed_out is True
    assert result.passed is False


def test_a_result_is_reused_only_for_the_same_tree(tmp_path: Path) -> None:
    _declare_gate(tmp_path)
    gate = _ScriptedGate(2, "red")

    first = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_A))
    again = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_A))
    rebased = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_B))

    assert first is not None
    assert again is not None
    assert (again.tree_sha, again.exit_code, again.output_tail) == (
        first.tree_sha,
        first.exit_code,
        first.output_tail,
    )
    assert again.consecutive_failures == first.consecutive_failures
    assert rebased is not None
    assert rebased.tree_sha == _TREE_B
    assert len(gate.calls) == 2
    record = json.loads(publication_gate_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record["tree_sha"] == _TREE_B


@pytest.mark.parametrize("commit_phase", ["development_commit", "development_final_commit"])
def test_a_failed_gate_routes_the_run_back_into_development_with_feedback(
    commit_phase: str,
) -> None:
    policy = cached_default_policy_bundle().pipeline
    assert publication_gate_loop_phase(policy, commit_phase) == "development_analysis"
    state = PipelineState.from_policy(policy).copy_with(phase=commit_phase)
    detail = "Publication gate `make -C ralph-workflow verify` failed (exit 2): ruff failed"

    routed, _effects = reduce(
        state, PublicationGateFailed(phase=commit_phase, detail=detail), policy
    )

    assert routed.phase == "development"
    assert routed.phase != "failed_terminal"
    assert routed.last_error is not None
    assert "ruff failed" in routed.last_error
    assert routed.get_loop_iteration("development_analysis_iteration") == 1


def test_repeated_gate_failures_stop_routing_back_once_the_budget_is_spent(
    tmp_path: Path,
) -> None:
    _declare_gate(tmp_path)
    trees = [f"{index:040x}" for index in range(1, MAX_PUBLICATION_GATE_FAILURES + 2)]
    results = [
        check_publication_gate(tmp_path, "HEAD", runner=_ScriptedGate(2), tree_of=_trees(tree))
        for tree in trees
    ]

    assert all(result is not None and not result.passed for result in results)
    routes = [result.routes_back_to_development for result in results if result is not None]
    assert routes == [True] * (MAX_PUBLICATION_GATE_FAILURES + 1)
    assert results[-1] is not None
    assert results[-1].consecutive_failures == MAX_PUBLICATION_GATE_FAILURES + 1

    green = check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(0), tree_of=_trees("f" * 40)
    )
    red_again = check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(2), tree_of=_trees("e" * 40)
    )
    assert green is not None
    assert green.passed
    assert red_again is not None
    assert red_again.routes_back_to_development is True


def test_a_blocked_publication_hands_the_gate_output_to_the_development_prompt(
    tmp_path: Path,
) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(2, "mypy: 3 errors\n"), tree_of=_trees(_TREE_A)
    )

    event = publication_gate_feedback(tmp_path, policy, "development_commit")

    assert event == PublicationGateFailed(phase="development_commit", detail=event.detail)
    assert "mypy: 3 errors" in event.detail
    hint = tmp_path / retry_hint_path("development", pipeline_policy=policy)
    assert "mypy: 3 errors" in hint.read_text(encoding="utf-8")


def test_red_evaluations_from_non_commit_landings_never_starve_the_commit_feedback(
    tmp_path: Path,
) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    for index in range(MAX_PUBLICATION_GATE_FAILURES * 2 + 2):
        check_publication_gate(
            tmp_path,
            "HEAD",
            runner=_ScriptedGate(1, "[verify] FAILED: Open decision numbering\n"),
            tree_of=_trees(f"{index + 1:040x}"),
        )

    event = publication_gate_feedback(tmp_path, policy, "development_commit")

    assert event is not None
    assert "Open decision numbering" in event.detail
    hint = tmp_path / retry_hint_path("development", pipeline_policy=policy)
    assert "Open decision numbering" in hint.read_text(encoding="utf-8")


def test_a_planning_entry_refusal_leaves_the_gate_failure_for_development(
    tmp_path: Path,
) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    check_publication_gate(
        tmp_path,
        "HEAD",
        runner=_ScriptedGate(1, "[verify] FAILED: Open decision numbering\n"),
        tree_of=_trees(_TREE_A),
    )

    assert leave_publication_gate_feedback(tmp_path, policy) is True
    assert leave_publication_gate_feedback(tmp_path, policy) is False

    hint = tmp_path / retry_hint_path("development", pipeline_policy=policy)
    assert "Open decision numbering" in hint.read_text(encoding="utf-8")
    record = json.loads(publication_gate_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record["route_backs"] == 1


def test_no_feedback_without_a_recorded_gate_failure(tmp_path: Path) -> None:
    policy = cached_default_policy_bundle().pipeline

    assert publication_gate_feedback(tmp_path, policy, "development_commit") is None


def test_route_backs_on_an_unchanged_tree_are_bounded(tmp_path: Path) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    check_publication_gate(tmp_path, "HEAD", runner=_ScriptedGate(2), tree_of=_trees(_TREE_A))

    routed = [
        publication_gate_feedback(tmp_path, policy, "development_commit")
        for _ in range(MAX_PUBLICATION_GATE_FAILURES + 1)
    ]

    assert all(event is not None for event in routed[:MAX_PUBLICATION_GATE_FAILURES])
    assert routed[-1] is None
    result = check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(2), tree_of=_trees(_TREE_A)
    )
    assert result is not None
    assert result.consecutive_failures == 1


def test_the_blocked_tree_is_reported_without_running_or_counting_the_gate(
    tmp_path: Path,
) -> None:
    _declare_gate(tmp_path)
    gate = _ScriptedGate(2, "red")
    check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_A))

    blocked = [blocked_publication(tmp_path, "HEAD", tree_of=_trees(_TREE_A)) for _ in range(10)]

    assert all(result is not None and result.tree_sha == _TREE_A for result in blocked)
    assert len(gate.calls) == 1
    assert blocked_publication(tmp_path, "HEAD", tree_of=_trees(_TREE_B)) is None
    check_publication_gate(tmp_path, "HEAD", runner=_ScriptedGate(0), tree_of=_trees(_TREE_B))
    assert blocked_publication(tmp_path, "HEAD", tree_of=_trees(_TREE_B)) is None
    record = json.loads(publication_gate_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record["consecutive_failures"] == 0


_SIGNATURE_A = "[verify] FAILED: Open decision numbering\n"
_SIGNATURE_B = (
    "[verify] FAILED: RSpec shard 4/4 (exit 1)\n"
    "[verify] FAILED: RSpec phase or a concurrent static lane\n"
)


def _red(tmp_path: Path, index: int, output: str) -> None:
    check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(1, output), tree_of=_trees(f"{index:040x}")
    )


def test_a_new_gate_failure_gets_its_own_route_back_budget(tmp_path: Path) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    for index in range(1, MAX_PUBLICATION_GATE_FAILURES + 1):
        _red(tmp_path, index, f"took {index}.{index}s\n{_SIGNATURE_A}")
        assert publication_gate_feedback(tmp_path, policy, "development_commit") is not None
    _red(tmp_path, 100, _SIGNATURE_A)
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is None

    _red(tmp_path, 101, _SIGNATURE_B)
    event = publication_gate_feedback(tmp_path, policy, "development_commit")

    assert event is not None
    assert "RSpec shard 4/4" in event.detail
    record = json.loads(publication_gate_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record["route_backs"] == 1
    assert record["failure_signature"]


def test_an_unchanged_gate_failure_stops_routing_back_after_its_budget(tmp_path: Path) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    routed = []
    for index in range(1, MAX_PUBLICATION_GATE_FAILURES + 3):
        _red(tmp_path, index, f"[verify] step took {index * 7} s\n{_SIGNATURE_B}")
        routed.append(publication_gate_feedback(tmp_path, policy, "development_commit"))

    assert all(event is not None for event in routed[:MAX_PUBLICATION_GATE_FAILURES])
    assert routed[MAX_PUBLICATION_GATE_FAILURES:] == [None, None]


def test_unstructured_output_keeps_its_signature_across_timing_noise(tmp_path: Path) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    routed = []
    for index in range(1, MAX_PUBLICATION_GATE_FAILURES + 3):
        _red(tmp_path, index, f"lint error in app.py\nfinished in {index * 3}.{index}s\n")
        routed.append(publication_gate_feedback(tmp_path, policy, "development_commit"))

    assert routed[MAX_PUBLICATION_GATE_FAILURES:] == [None, None]


_CANCELLED = "........\nRan 8 tests in 2.688s\n\nOK\n[verify] CANCELLED: received SIGTERM\n"


@pytest.mark.parametrize(
    ("exit_code", "output"), [(-15, _CANCELLED), (-9, "killed\n"), (1, _CANCELLED)]
)
def test_a_gate_killed_by_a_signal_is_inconclusive_and_never_routes_back(
    tmp_path: Path, exit_code: int, output: str
) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)

    result = check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(exit_code, output), tree_of=_trees(_TREE_A)
    )

    assert result is not None
    assert result.inconclusive is True
    assert result.block is PublicationBlock.GATE_FAILED
    assert result.routes_back_to_development is False
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is None
    assert leave_publication_gate_feedback(tmp_path, policy) is False
    assert not (tmp_path / retry_hint_path("development", pipeline_policy=policy)).exists()


def test_an_inconclusive_gate_is_rerun_on_the_same_tree_a_bounded_number_of_times(
    tmp_path: Path,
) -> None:
    _declare_gate(tmp_path)
    gate = _ScriptedGate(-15, _CANCELLED)

    for _ in range(MAX_INCONCLUSIVE_GATE_RUNS + 3):
        check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_A))

    assert len(gate.calls) == MAX_INCONCLUSIVE_GATE_RUNS
    green = check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(0), tree_of=_trees(_TREE_B)
    )
    assert green is not None
    assert green.passed


def test_an_inconclusive_run_neither_charges_nor_resets_the_failure_budget(
    tmp_path: Path,
) -> None:
    policy = cached_default_policy_bundle().pipeline
    _declare_gate(tmp_path)
    _red(tmp_path, 1, _SIGNATURE_B)
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is not None
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is not None

    check_publication_gate(
        tmp_path, "HEAD", runner=_ScriptedGate(-15, _CANCELLED), tree_of=_trees(f"{2:040x}")
    )
    _red(tmp_path, 3, _SIGNATURE_B)

    record = json.loads(publication_gate_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record["route_backs"] == 2
    assert record["inconclusive_runs"] == 0


def test_a_legacy_record_without_a_signature_still_loads_with_its_counters(
    tmp_path: Path,
) -> None:
    _declare_gate(tmp_path)
    publication_gate_record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    publication_gate_record_path(tmp_path).write_text(
        json.dumps(
            {
                "command": "make -C ralph-workflow verify",
                "tree_sha": _TREE_A,
                "exit_code": 1,
                "timed_out": False,
                "output_tail": _SIGNATURE_B,
                "consecutive_failures": 16,
                "route_backs": 5,
            }
        ),
        encoding="utf-8",
    )
    gate = _ScriptedGate(1, _SIGNATURE_B)

    reused = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_A))
    fresh = check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_B))

    assert reused is not None
    assert reused.consecutive_failures == 16
    assert gate.calls == [gate.calls[0]]
    assert fresh is not None
    assert fresh.consecutive_failures == 17
    assert fresh.route_backs == 0


def test_a_passed_gate_record_is_reused_for_the_same_tree(tmp_path: Path) -> None:
    _declare_gate(tmp_path)
    gate = _ScriptedGate(0, "[verify] All checks passed.\n")

    for _ in range(3):
        check_publication_gate(tmp_path, "HEAD", runner=gate, tree_of=_trees(_TREE_A))

    assert len(gate.calls) == 1
