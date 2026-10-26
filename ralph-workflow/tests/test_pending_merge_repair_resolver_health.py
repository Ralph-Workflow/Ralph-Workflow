"""Pending-merge repair offers repair agents by OBSERVED health, never by invocation.

Every scenario here drives the public seam
(:func:`handoff_pending_merge_repair`) and asserts only on what is
observable: which agent ran, how often, and what the integration record
says afterwards.

The facts the shared owner is told are Ralph's own: the repair
declaration the agent left on disk, and whether the workspace changed
while the attempt ran. The invoke boolean is NOT a fact -- an
invocation can report success after producing nothing, and report
failure after producing the repair -- so it never decides an agent's
standing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline._pending_merge_repair import handoff_pending_merge_repair
from ralph.pipeline.auto_integrate_record import IntegrationRecord, record_path
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests.test_auto_integrate_record_idempotent import _RecordBackend

if TYPE_CHECKING:
    from ralph.workspace.probe import WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace

ROOT = Path("/workspace")
CHAIN = ("first", "second")
DECLARATION = ROOT / ".agent" / "tmp" / "pending_commit_repair_paths.json"


class _ChangingWorkspaceProbe:
    """A workspace probe that observes the workspace changing on every attempt."""

    def __init__(self) -> None:
        self.changed = True

    def observe_before(self, phase: str, workspace: Workspace) -> None:
        del phase, workspace

    def has_changed(self, phase: str, workspace: Workspace) -> bool:
        del phase, workspace
        return self.changed


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
    declares_repair: bool = False,
) -> bool:
    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        if raises:
            raise RuntimeError("quota: You've hit your usage limit")
        if declares_repair:
            backend.write_text(DECLARATION, '["src/fixed.py"]')
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


def _controller(
    clock: FakeClock, *, probe: WorkspaceChangeProbe | None = None
) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock, workspace_probe=probe))


def test_truthy_invocation_without_a_repair_is_an_unproductive_attempt() -> None:
    """§3.5/§7.1: the invoke boolean is not a fact; the OBSERVED artifact is.

    The agent's invocation reports success every time, but it leaves no
    repair declaration behind, so Ralph observed no completion. The
    owner's consecutive limit decides when the agent is benched, and the
    next candidate runs.
    """
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(3):
        _run(backend, controller, invoked, succeeds=True)
    _run(backend, controller, invoked, succeeds=True)

    assert invoked == ["first", "first", "first", "second"]


def test_declared_repair_is_a_verified_artifact_and_is_never_benched() -> None:
    """§4.1/§4.3: an accepted artifact clears the agent's history."""
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(12):
        _run(backend, controller, invoked, declares_repair=True)

    assert invoked == ["first"] * 12


def test_agent_that_changes_the_workspace_is_demoted_only_by_the_total_limit() -> None:
    """§4.3: a workspace change restarts the consecutive count, not the total."""
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock(), probe=_ChangingWorkspaceProbe())

    for _ in range(9):
        _run(backend, controller, invoked)
    _run(backend, controller, invoked)

    assert invoked == ["first"] * 9 + ["second"]


def test_false_invocation_with_a_verified_artifact_is_not_a_failure() -> None:
    """§3.5: the invoke boolean is not a fact.

    The invocation reports failure, but the repair declaration Ralph
    asked for is on disk: the OBSERVED completion clears the agent's
    history, so the agent is offered again instead of being benched.
    """
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(4):
        _run(backend, controller, invoked, succeeds=False, declares_repair=True)

    assert invoked == ["first"] * 4


def test_all_agents_cooling_down_invokes_nothing_and_spends_no_attempt() -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())
    for _ in range(3):
        _run(backend, controller, invoked)
    for _ in range(3):
        _run(backend, controller, invoked)
    before = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(ROOT)))

    for _ in range(20):
        assert _run(backend, controller, invoked) is False

    after = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(ROOT)))
    assert invoked == ["first"] * 3 + ["second"] * 3
    assert after.merge_commit_repair_attempts == before.merge_commit_repair_attempts
    # The integration is still PENDING with its progress retained: the
    # pending merge and its verified tree are untouched.
    assert after.merge_commit_pending is True
    assert after.merge_commit_tree == "tree"


def test_agent_that_raises_is_benched_too() -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(3):
        _run(backend, controller, invoked, raises=True)
    _run(backend, controller, invoked)

    assert invoked == ["first", "first", "first", "second"]


def test_agent_returns_after_its_cooldown() -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    clock = FakeClock()
    controller = _controller(clock)
    for _ in range(3):
        _run(backend, controller, invoked)

    clock.advance(3600.0)
    _run(backend, controller, invoked)

    assert invoked == ["first", "first", "first", "first"]


def test_successful_repairs_of_unrelated_integrations_never_accumulate_a_cooldown() -> None:
    """§4.3: a verified repair clears the history, so no cooldown accumulates."""
    controller = _controller(FakeClock())
    invoked: list[str] = []

    for _ in range(12):
        _run(_RecordBackend(), controller, invoked, declares_repair=True)

    assert invoked == ["first"] * 12


def test_integration_phase_standing_does_not_leak_into_other_phases() -> None:
    """§7.1: the integration phase has its own (phase, agent) identity."""
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())
    for _ in range(3):
        _run(backend, controller, invoked)

    assert controller.ledger.status(PHASE_RESOLUTION, "first").is_cooldown is True
    assert controller.ledger.status("development", "first").is_eligible is True
