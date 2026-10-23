"""The retained-integration diagnostic must not keep calling a resolver that gets nowhere."""

from __future__ import annotations

from pathlib import Path

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.operation_evidence import InMemoryOperationEvidenceCollector, OperationFact
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

CHAIN = ("codex/a", "cursor/auto", "opencode/b")


def _record(root: Path, attempts: int = 0) -> IntegrationRecord:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        resolving_rebase=True,
        merge_commit_repair_attempts=attempts,
        owning_worktree=str(root),
    )
    write_record(root, record)
    return record


def _collector() -> InMemoryOperationEvidenceCollector:
    return InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock))


def _handoff(
    root: Path,
    controller: RecoveryController,
    invoked: list[str],
    *,
    succeeds: bool = False,
) -> bool:
    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return succeeds

    record = read_record(root)
    assert record is not None
    return diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="retained resolution lacks verified completion proof",
        agents=CHAIN,
        invoke=invoke,
        collector=_collector(),
        controller=controller,
    )


def test_first_failure_moves_the_next_pass_to_the_next_candidate(tmp_path: Path) -> None:
    _record(tmp_path)
    controller = _controller(FakeClock())
    invoked: list[str] = []

    _handoff(tmp_path, controller, invoked)
    _handoff(tmp_path, controller, invoked)

    assert invoked == ["codex/a", "cursor/auto"]


def test_agent_that_keeps_failing_is_not_called_again_while_cooling_down(tmp_path: Path) -> None:
    _record(tmp_path)
    controller = _controller(FakeClock())
    invoked: list[str] = []

    for _ in range(3):
        _handoff(tmp_path, controller, invoked)
    _handoff(tmp_path, controller, invoked)

    assert invoked == ["codex/a", "cursor/auto", "opencode/b"]


def test_all_candidates_cooling_down_invokes_nothing_and_spends_no_attempt(
    tmp_path: Path,
) -> None:
    _record(tmp_path)
    controller = _controller(FakeClock())
    invoked: list[str] = []
    for _ in range(3):
        _handoff(tmp_path, controller, invoked)
    saved = read_record(tmp_path)
    assert saved is not None
    attempts_before = saved.merge_commit_repair_attempts

    for _ in range(20):
        assert _handoff(tmp_path, controller, invoked) is False

    assert len(invoked) == 3
    after = read_record(tmp_path)
    assert after is not None
    assert after.merge_commit_repair_attempts == attempts_before


def test_candidate_is_offered_again_after_its_cooldown(tmp_path: Path) -> None:
    _record(tmp_path)
    clock = FakeClock()
    controller = _controller(clock)
    invoked: list[str] = []
    for _ in range(3):
        _handoff(tmp_path, controller, invoked)

    clock.advance(3600.0)
    _handoff(tmp_path, controller, invoked)

    assert invoked[-1] == "codex/a"
    assert len(invoked) == 4


def test_cursor_usage_limit_failure_benches_cursor_for_every_later_pass(tmp_path: Path) -> None:
    _record(tmp_path)
    clock = FakeClock()
    controller = _controller(clock)
    invoked: list[str] = []

    def quota_invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return False

    record = read_record(tmp_path)
    assert record is not None
    diagnostic.handoff_integration_diagnostic(
        root=tmp_path,
        record=record,
        failure="x",
        agents=("cursor/auto", "opencode/b"),
        invoke=quota_invoke,
        collector=_collector(),
        controller=controller,
        failure_detail=lambda: "ActionRequiredError: You've hit your usage limit",
    )
    clock.advance(30.0)
    record = read_record(tmp_path)
    assert record is not None
    diagnostic.handoff_integration_diagnostic(
        root=tmp_path,
        record=record,
        failure="x",
        agents=("cursor/auto", "opencode/b"),
        invoke=quota_invoke,
        collector=_collector(),
        controller=controller,
    )

    assert invoked == ["cursor/auto", "opencode/b"]


def test_agent_whose_invocation_raises_is_benched_before_the_error_propagates(
    tmp_path: Path,
) -> None:
    _record(tmp_path)
    controller = _controller(FakeClock())
    invoked: list[str] = []

    def raising_invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        raise RuntimeError("worktree vanished")

    record = read_record(tmp_path)
    assert record is not None
    with pytest.raises(RuntimeError, match="worktree vanished"):
        diagnostic.handoff_integration_diagnostic(
            root=tmp_path,
            record=record,
            failure="x",
            agents=CHAIN,
            invoke=raising_invoke,
            collector=_collector(),
            controller=controller,
        )

    _handoff(tmp_path, controller, invoked)
    assert invoked == ["codex/a", "cursor/auto"]
