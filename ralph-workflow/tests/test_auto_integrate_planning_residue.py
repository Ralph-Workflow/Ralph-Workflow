"""Planning-residue seam routing tests.

:func:`ralph.pipeline.auto_integrate_planning.resolve_planning_residue`
is the run loop's post-dispatch residue seam. It must consume the typed
:class:`~ralph.pipeline.integration_cleanup.ResidueCleanupOutcome`
instead of discarding it: an in-progress git operation
(``operation_blocked`` / ``operation_unknown``) routes to the operation
ladder through the ONE owner seam — never to residue restore and never
to the conflict resolver — and every other outcome is returned untyped-
loss free so the caller can act on it.

The git effects run through a fake :class:`CleanupGitPort` and the
ledger through a fake :class:`ResidueLedgerPort` (both installed in
place of the production port classes); the owner seam is a recording
stand-in. No real Git, no subprocess, no filesystem, no sleeps.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import integration_cleanup
from ralph.pipeline import integration_owner_seam as owner_seam
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline.auto_integrate_planning import resolve_planning_residue
from ralph.pipeline.integration_cleanup import PlanningBaseline
from ralph.pipeline.integration_decision import NextAction
from ralph.pipeline.integration_observation import InProgressKind, Reading

pytestmark = pytest.mark.timeout_seconds(10)

_ROOT = Path("/fake/root")

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _baseline() -> PlanningBaseline:
    return PlanningBaseline(
        head_sha="h" * 40,
        status_blob="",
        tracked_diff_hash="d" * 64,
        fingerprint="f" * 64,
        human_work_ref=None,
    )


class _FakeCleanupGit:
    """Minimal fake CleanupGitPort recording every restore-class effect."""

    def __init__(self, in_progress: Reading[InProgressKind]) -> None:
        self._in_progress = in_progress
        self.restore_calls = 0
        self.snapshot_calls = 0
        self.quarantine_calls = 0

    def porcelain_status_z(self, root: Path) -> str | None:
        del root
        return ""

    def tracked_diff_hash(self, root: Path) -> str | None:
        del root
        return "d" * 64

    def head_sha(self, root: Path) -> str | None:
        del root
        return "h" * 40

    def in_progress_kind(self, root: Path) -> Reading[InProgressKind]:
        del root
        return self._in_progress

    def snapshot_worktree(self, root: Path, *, head_sha: str) -> str:
        del root, head_sha
        self.snapshot_calls += 1
        return "refs/ralph-fake/backup"

    def ref_exists(self, root: Path, ref: str) -> bool:
        del root, ref
        return True

    def restore_paths(self, root: Path, *, source: str, paths: Sequence[str]) -> bool:
        del root, source, paths
        self.restore_calls += 1
        return True

    def preserve_dirt_commit(
        self, root: Path, *, head_sha: str, paths: Sequence[str]
    ) -> str | None:
        del root, head_sha, paths
        return None

    def quarantine_paths(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        quarantine_dir: Path,
    ) -> tuple[str, ...]:
        del root, quarantine_dir
        self.quarantine_calls += 1
        return tuple(paths)

    def incoming_overwrite_paths(self, root: Path, target: str | None) -> frozenset[str]:
        del root, target
        return frozenset()

    def load_state(self, root: Path) -> Mapping[str, object] | None:
        del root
        return None

    def store_state(self, root: Path, payload: Mapping[str, object]) -> None:
        del root, payload

    def clear_state(self, root: Path) -> None:
        del root

    def path_exists(self, path: Path) -> bool:
        del path
        return False

    def move_path(self, source: str, destination: Path) -> None:
        del source, destination


class _FakeLedger:
    """Fake ResidueLedgerPort; the operation gate never charges it."""

    def __init__(self) -> None:
        self.charges = 0

    def charge_residue_attempt(self, root: Path, *, residue: object) -> NextAction:
        del root, residue
        self.charges += 1
        return NextAction.SAVE_AND_RESTORE


def _install_ports(
    monkeypatch: pytest.MonkeyPatch,
    fake_git: _FakeCleanupGit,
    fake_ledger: _FakeLedger,
) -> None:
    """Install the fake ports in place of the production port classes."""
    monkeypatch.setattr(integration_cleanup, "RunGitCleanupPort", lambda: fake_git)
    monkeypatch.setattr(integration_cleanup, "OwnerResidueLedgerPort", lambda: fake_ledger)


def test_operation_blocked_routes_to_the_operation_ladder_without_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A merge in progress: no restore, no snapshot, owner seam asked once."""
    fake_git = _FakeCleanupGit(Reading.known(InProgressKind.MERGE))
    fake_ledger = _FakeLedger()
    _install_ports(monkeypatch, fake_git, fake_ledger)
    routed: list[tuple[Path, IntegrationAction | None]] = []

    def _fake_decide_seam_action(
        root: Path,
        *,
        record: object = None,
        agent_active: bool = False,
        ledger_path: Path | None = None,
        retained: bool = False,
        target: str | None = None,
        remote: str | None = None,
    ) -> IntegrationAction:
        del record, agent_active, ledger_path, retained, target, remote
        action = IntegrationAction.CONTINUE_GIT_OPERATION
        routed.append((root, action))
        return action

    monkeypatch.setattr(owner_seam, "decide_seam_action", _fake_decide_seam_action)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_record.read_record",
        lambda _root: None,
    )

    outcome = resolve_planning_residue(_ROOT, _baseline(), target="main")

    assert outcome is not None
    assert outcome.operation_blocked is True
    assert outcome.operation_kind is InProgressKind.MERGE
    # The operation owns the index: cleanup took NO restore/preservation
    # action and the ledger was never charged.
    assert fake_git.restore_calls == 0
    assert fake_git.snapshot_calls == 0
    assert fake_git.quarantine_calls == 0
    assert fake_ledger.charges == 0
    # The operation state went to the ONE owner seam (the operation
    # ladder), exactly once — never to a resolver dispatch.
    assert routed == [(_ROOT, IntegrationAction.CONTINUE_GIT_OPERATION)]


def test_operation_unknown_fails_closed_to_the_operation_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable operation probe routes fail-closed, still no restore."""
    fake_git = _FakeCleanupGit(Reading.unknown("probe timed out"))
    fake_ledger = _FakeLedger()
    _install_ports(monkeypatch, fake_git, fake_ledger)
    routed: list[IntegrationAction] = []

    def _fake_decide_seam_action(root: Path, **kwargs: object) -> IntegrationAction:
        del root, kwargs
        action = IntegrationAction.RESOLVE_ACTIVE_OPERATION
        routed.append(action)
        return action

    monkeypatch.setattr(owner_seam, "decide_seam_action", _fake_decide_seam_action)

    outcome = resolve_planning_residue(_ROOT, _baseline(), target="main")

    assert outcome is not None
    assert outcome.operation_unknown is True
    assert outcome.operation_kind is None
    assert fake_git.restore_calls == 0
    assert fake_ledger.charges == 0
    assert routed == [IntegrationAction.RESOLVE_ACTIVE_OPERATION]


def test_clean_outcome_is_returned_without_operation_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean dispatch returns the typed outcome and never asks the owner."""
    fake_git = _FakeCleanupGit(Reading.known(InProgressKind.NONE))
    fake_ledger = _FakeLedger()
    _install_ports(monkeypatch, fake_git, fake_ledger)

    def _boom(root: Path, **kwargs: object) -> IntegrationAction:
        raise AssertionError(f"owner seam must not be asked for a clean tree: {root} {kwargs}")

    monkeypatch.setattr(owner_seam, "decide_seam_action", _boom)

    outcome = resolve_planning_residue(_ROOT, _baseline(), target="main")

    assert outcome is not None
    assert outcome.clean is True
    assert outcome.operation_blocked is False
    assert outcome.operation_unknown is False
    assert fake_ledger.charges == 0
