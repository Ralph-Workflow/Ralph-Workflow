"""Pending-merge repair offers repair agents by health, never by attempt count."""

from __future__ import annotations

from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline._pending_merge_repair import handoff_pending_merge_repair
from ralph.pipeline.auto_integrate_record import IntegrationRecord, record_path
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests.test_auto_integrate_record_idempotent import _RecordBackend

ROOT = Path("/workspace")
CHAIN = ("first", "second")


def _record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="original",
        pre_target_sha="target",
        merge_commit_pending=True,
        merge_commit_head="head",
        merge_commit_parent="parent",
        merge_commit_tree="tree",
    )


def _run(
    backend: _RecordBackend,
    controller: RecoveryController,
    invoked: list[str],
    *,
    raises: bool = False,
    succeeds: bool = False,
) -> bool:
    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        if raises:
            raise RuntimeError("quota: You've hit your usage limit")
        return succeeds

    raw = backend.read_bytes(record_path(ROOT)) if backend.exists(record_path(ROOT)) else None
    record = IntegrationRecord.model_validate_json(raw) if raw is not None else _record()
    return handoff_pending_merge_repair(
        root=ROOT,
        record=record,
        failure="pre-commit: formatter executable missing",
        agents=CHAIN,
        invoke=invoke,
        backend=backend,
        controller=controller,
    )


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock))


def test_failed_repair_agent_is_replaced_by_the_next_candidate() -> None:
    backend, invoked = _RecordBackend(), []

    controller = _controller(FakeClock())
    _run(backend, controller, invoked)
    _run(backend, controller, invoked)

    assert invoked == ["first", "second"]


def test_all_agents_cooling_down_invokes_nothing_and_spends_no_attempt() -> None:
    backend, invoked = _RecordBackend(), []
    controller = _controller(FakeClock())
    _run(backend, controller, invoked)
    _run(backend, controller, invoked)
    before = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(ROOT)))

    for _ in range(20):
        assert _run(backend, controller, invoked) is False

    after = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(ROOT)))
    assert invoked == ["first", "second"]
    assert after.merge_commit_repair_attempts == before.merge_commit_repair_attempts


def test_agent_that_raises_is_benched_too() -> None:
    backend, invoked = _RecordBackend(), []
    controller = _controller(FakeClock())

    _run(backend, controller, invoked, raises=True)
    _run(backend, controller, invoked)

    assert invoked == ["first", "second"]


def test_agent_returns_after_its_cooldown() -> None:
    backend, invoked = _RecordBackend(), []
    clock = FakeClock()
    controller = _controller(clock)
    _run(backend, controller, invoked)
    _run(backend, controller, invoked)

    clock.advance(3600.0)
    _run(backend, controller, invoked)

    assert invoked == ["first", "second", "first"]


def test_successful_repairs_of_unrelated_integrations_never_accumulate_a_cooldown() -> None:
    controller = _controller(FakeClock())
    invoked: list[str] = []

    for _ in range(12):
        _run(_RecordBackend(), controller, invoked, succeeds=True)

    assert invoked == ["first"] * 12


def test_success_that_leaves_the_same_operation_pending_benches_the_agent() -> None:
    backend, invoked = _RecordBackend(), []
    controller = _controller(FakeClock())

    _run(backend, controller, invoked, succeeds=True)
    _run(backend, controller, invoked, succeeds=True)
    _run(backend, controller, invoked, succeeds=True)

    assert invoked == ["first", "first", "second"]
