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

CHAIN = ("first", "second")

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_truthy_invocation_without_a_repair_is_an_unproductive_attempt": "the production handoff validates its target worktree, so the test now supplies the real Git repository fixture required by that public boundary",
    "test_declared_repair_is_a_verified_artifact_and_never_starts_a_cooldown": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_declared_repair_is_a_verified_artifact_and_is_never_benched with an unchanged body"
    ),
    "test_agent_that_changes_the_workspace_cools_down_only_at_the_total_limit": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_agent_that_changes_the_workspace_is_demoted_only_by_the_total_limit with an unchanged body"
    ),
    "test_agent_that_raises_cools_down_too": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_agent_that_raises_is_benched_too with an unchanged body"
    ),
    "test_false_invocation_with_a_verified_artifact_is_not_a_failure": "the production handoff validates its target worktree, so the test now supplies the real Git repository fixture required by that public boundary",
    "test_all_agents_cooling_down_invokes_nothing_and_spends_no_attempt": "the production handoff validates its target worktree, so the test now supplies the real Git repository fixture required by that public boundary",
    "test_agent_returns_after_its_cooldown": "the production handoff validates its target worktree, so the test now supplies the real Git repository fixture required by that public boundary",
    "test_successful_repairs_of_unrelated_integrations_never_accumulate_a_cooldown": "the production handoff validates its target worktree, so the test now supplies the real Git repository fixture required by that public boundary",
    "test_integration_phase_standing_does_not_leak_into_other_phases": (
        "operator rule: an agent is either on cooldown or not; the cooldown is keyed by agent "
        "only, so a resolution unproductive_limit cooldown also applies in development, and "
        "the test asserts the shared cooldown and that both phases see it expire"
    ),
    "test_integration_cooldown_applies_in_every_phase_until_it_expires": (
        "operator rule: an agent is either on cooldown or not; replaces the phase isolation "
        "test with the agent-keyed cooldown contract"
    ),
}


class _ChangingWorkspaceProbe:
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
    root: Path,
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
            declaration = root / ".agent" / "tmp" / "pending_commit_repair_paths.json"
            backend.write_text(declaration, '["src/fixed.py"]')
        return succeeds

    raw = backend.read_bytes(record_path(root)) if backend.exists(record_path(root)) else None
    record = IntegrationRecord.model_validate_json(raw) if raw is not None else _record()
    return handoff_pending_merge_repair(
        root=root,
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


def test_truthy_invocation_without_a_repair_is_an_unproductive_attempt(
    tmp_git_repo: Path,
) -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(3):
        _run(tmp_git_repo, backend, controller, invoked, succeeds=True)
    _run(tmp_git_repo, backend, controller, invoked, succeeds=True)

    assert invoked == ["first", "first", "first", "second"]


def test_declared_repair_is_a_verified_artifact_and_never_starts_a_cooldown(
    tmp_git_repo: Path,
) -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(12):
        _run(tmp_git_repo, backend, controller, invoked, declares_repair=True)

    assert invoked == ["first"] * 12


def test_agent_that_changes_the_workspace_cools_down_only_at_the_total_limit(
    tmp_git_repo: Path,
) -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock(), probe=_ChangingWorkspaceProbe())

    for _ in range(9):
        _run(tmp_git_repo, backend, controller, invoked)
    _run(tmp_git_repo, backend, controller, invoked)

    assert invoked == ["first"] * 9 + ["second"]


def test_false_invocation_with_a_verified_artifact_is_not_a_failure(
    tmp_git_repo: Path,
) -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(4):
        _run(
            tmp_git_repo,
            backend,
            controller,
            invoked,
            succeeds=False,
            declares_repair=True,
        )

    assert invoked == ["first"] * 4


def test_all_agents_cooling_down_invokes_nothing_and_spends_no_attempt(
    tmp_git_repo: Path,
) -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())
    for _ in range(3):
        _run(tmp_git_repo, backend, controller, invoked)
    for _ in range(3):
        _run(tmp_git_repo, backend, controller, invoked)
    before = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(tmp_git_repo)))

    for _ in range(20):
        assert _run(tmp_git_repo, backend, controller, invoked) is False

    after = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(tmp_git_repo)))
    assert invoked == ["first"] * 3 + ["second"] * 3
    assert after.merge_commit_repair_attempts == before.merge_commit_repair_attempts
    assert after.merge_commit_pending is True
    assert after.merge_commit_tree == "tree"


def test_agent_that_raises_cools_down_too(tmp_git_repo: Path) -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    controller = _controller(FakeClock())

    for _ in range(3):
        _run(tmp_git_repo, backend, controller, invoked, raises=True)
    _run(tmp_git_repo, backend, controller, invoked)

    assert invoked == ["first", "first", "first", "second"]


def test_agent_returns_after_its_cooldown(tmp_git_repo: Path) -> None:
    backend = _RecordBackend()
    invoked: list[str] = []
    clock = FakeClock()
    controller = _controller(clock)
    for _ in range(3):
        _run(tmp_git_repo, backend, controller, invoked)

    clock.advance(3600.0)
    _run(tmp_git_repo, backend, controller, invoked)

    assert invoked == ["first", "first", "first", "first"]


def test_successful_repairs_of_unrelated_integrations_never_accumulate_a_cooldown(
    tmp_git_repo: Path,
) -> None:
    controller = _controller(FakeClock())
    invoked: list[str] = []

    for _ in range(12):
        _run(tmp_git_repo, _RecordBackend(), controller, invoked, declares_repair=True)

    assert invoked == ["first"] * 12


def test_integration_cooldown_applies_in_every_phase_until_it_expires(
    tmp_git_repo: Path,
) -> None:
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
    from ralph.recovery.agent_failure_ledger import Cooldown

    backend = _RecordBackend()
    invoked: list[str] = []
    clock = FakeClock()
    controller = _controller(clock)
    for _ in range(3):
        _run(tmp_git_repo, backend, controller, invoked)

    resolution_status = controller.ledger.status(PHASE_RESOLUTION, "first")
    assert isinstance(resolution_status, Cooldown)
    assert resolution_status.reason == "unproductive_limit"
    assert controller.ledger.status("development", "first") == resolution_status

    clock.advance((resolution_status.until_ms + 1) / 1000.0 - clock.monotonic())

    assert controller.ledger.status(PHASE_RESOLUTION, "first").is_eligible is True
    assert controller.ledger.status("development", "first").is_eligible is True
