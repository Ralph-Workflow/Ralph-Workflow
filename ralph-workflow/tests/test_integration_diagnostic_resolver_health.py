"""The retained-integration diagnostic must not keep calling a resolver that gets nowhere."""

from __future__ import annotations

from pathlib import Path

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.operation_evidence import InMemoryOperationEvidenceCollector, OperationFact
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests.test_auto_integrate_record_idempotent import _RecordBackend

pytestmark = pytest.mark.timeout_seconds(5)

CHAIN = ("codex/a", "cursor/auto", "opencode/b")


def _record(
    root: Path, attempts: int = 0, backend: _RecordBackend | None = None
) -> IntegrationRecord:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        resolving_rebase=True,
        merge_commit_repair_attempts=attempts,
        owning_worktree=str(root),
    )
    if backend is not None:
        write_record(root, record, backend=backend)
    else:
        write_record(root, record)
    return record


def _collector() -> InMemoryOperationEvidenceCollector:
    return InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )


@pytest.fixture(autouse=True)
def _stub_decide_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.pipeline import IntegrationAction

    monkeypatch.setattr(
        diagnostic,
        "decide_integration",
        lambda _root, record=None: (IntegrationAction.WAIT_FOR_BOUNDED_RETRY, "stubbed"),
    )


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock))


def _handoff(
    root: Path,
    controller: RecoveryController,
    invoked: list[str],
    *,
    succeeds: bool = False,
    repair_attempt_cap: int = 15,
    backend: _RecordBackend | None = None,
) -> bool:
    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return succeeds

    record = read_record(root, backend=backend) if backend is not None else read_record(root)
    assert record is not None
    kwargs = {"backend": backend} if backend is not None else {}
    return diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="retained resolution lacks verified completion proof",
        agents=CHAIN,
        invoke=invoke,
        collector=_collector(),
        controller=controller,
        repair_attempt_cap=repair_attempt_cap,
        **kwargs,
    )


def test_consecutive_unproductive_failures_move_to_next_candidate(tmp_path: Path) -> None:
    backend = _RecordBackend()
    _record(tmp_path, backend=backend)
    controller = _controller(FakeClock())
    invoked: list[str] = []

    for _ in range(3):
        _handoff(tmp_path, controller, invoked, backend=backend)
    _handoff(tmp_path, controller, invoked, backend=backend)

    assert invoked == ["codex/a", "codex/a", "codex/a", "cursor/auto"]


def test_agent_that_keeps_failing_is_not_called_again_while_cooling_down(tmp_path: Path) -> None:
    backend = _RecordBackend()
    _record(tmp_path, backend=backend)
    controller = _controller(FakeClock())
    invoked: list[str] = []

    for _ in range(9):
        _handoff(tmp_path, controller, invoked, backend=backend)
    _handoff(tmp_path, controller, invoked, backend=backend)

    assert invoked == ["codex/a"] * 3 + ["cursor/auto"] * 3 + ["opencode/b"] * 3


def test_all_candidates_cooling_down_invokes_nothing_and_spends_no_attempt(
    tmp_path: Path,
) -> None:
    backend = _RecordBackend()
    _record(tmp_path, backend=backend)
    controller = _controller(FakeClock())
    invoked: list[str] = []
    for _ in range(9):
        _handoff(tmp_path, controller, invoked, backend=backend)
    saved = read_record(tmp_path, backend=backend)
    assert saved is not None
    attempts_before = saved.merge_commit_repair_attempts

    for _ in range(5):
        assert _handoff(tmp_path, controller, invoked, backend=backend) is False

    assert len(invoked) == 9
    after = read_record(tmp_path, backend=backend)
    assert after is not None
    assert after.merge_commit_repair_attempts == attempts_before


def test_candidate_is_offered_again_after_its_cooldown(tmp_path: Path) -> None:
    backend = _RecordBackend()
    _record(tmp_path, backend=backend)
    clock = FakeClock()
    controller = _controller(clock)
    invoked: list[str] = []
    for _ in range(3):
        _handoff(tmp_path, controller, invoked, backend=backend)

    clock.advance(3600.0)
    _handoff(tmp_path, controller, invoked, backend=backend)

    assert invoked[-1] == "codex/a"
    assert len(invoked) == 4


def test_cursor_usage_limit_failure_benches_cursor_for_every_later_pass(tmp_path: Path) -> None:
    backend = _RecordBackend()
    _record(tmp_path, backend=backend)
    clock = FakeClock()
    controller = _controller(clock)
    invoked: list[str] = []

    def quota_invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return False

    for _ in range(3):
        record = read_record(tmp_path, backend=backend)
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
            backend=backend,
        )
    clock.advance(30.0)
    record = read_record(tmp_path, backend=backend)
    assert record is not None
    diagnostic.handoff_integration_diagnostic(
        root=tmp_path,
        record=record,
        failure="x",
        agents=("cursor/auto", "opencode/b"),
        invoke=quota_invoke,
        collector=_collector(),
        controller=controller,
        backend=backend,
    )

    assert invoked == ["cursor/auto", "cursor/auto", "cursor/auto", "opencode/b"]


def test_agent_whose_invocation_raises_is_benched_before_the_error_propagates(
    tmp_path: Path,
) -> None:
    backend = _RecordBackend()
    _record(tmp_path, backend=backend)
    controller = _controller(FakeClock())
    invoked: list[str] = []

    def raising_invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        raise RuntimeError("worktree vanished")

    for _ in range(3):
        record = read_record(tmp_path, backend=backend)
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
                backend=backend,
            )

    _handoff(tmp_path, controller, invoked, backend=backend)
    assert invoked == ["codex/a", "codex/a", "codex/a", "cursor/auto"]


def test_phase_histories_remain_independent(tmp_path: Path) -> None:
    """An agent cooling down in resolution remains eligible in development, and vice versa."""
    backend = _RecordBackend()
    _record(tmp_path, backend=backend)
    controller = _controller(FakeClock())
    invoked: list[str] = []

    # Fail codex/a 3 times in resolution -> demoted to cooldown in resolution
    for _ in range(3):
        _handoff(tmp_path, controller, invoked, backend=backend)

    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

    assert controller.ledger.status(PHASE_RESOLUTION, "codex/a").is_cooldown is True

    # In development phase, codex/a remains eligible
    assert controller.ledger.status("development", "codex/a").is_eligible is True
