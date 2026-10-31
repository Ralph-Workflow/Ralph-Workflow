"""Fast, deterministic proofs for the remote-sync advance decisions.

The real-Git contract lives in
``tests/test_auto_integrate_remote_sync_fast_forward_contract.py`` (see
``docs/agents/remote-sync.md``). This module pins the two decisions behind
the 2026-10-30 "remote sync does not fast-forward to origin" bug without
touching git, by substituting the public collaborators the catch-up tick and
the publication reconciler bind:

* a catch-up tick inside the fetch cadence (no fetch allowed) still applies a
  remote-tracking ref that is STRICTLY ahead -- and nothing else;
* the publication reconciler advances the TARGET itself when the remote is
  ahead instead of delegating to the throttled, lease-taking catch-up tick,
  and reports a refusal by its real cause rather than "unreachable";
* while an agent phase runs in a checkout, no tick moves THAT checkout (the
  run checkout, or the target's checkout in the trunk layout), yet the fetch
  and the advance of a target checked out elsewhere continue, the deferral
  never ages into a stall, and the first tick after the phase lands the
  deferred move. The runner marks the phase on every exit path.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import WORKTREE_FOUND, WORKTREE_NOT_CHECKED_OUT
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline import runner as runner_module
from ralph.pipeline.auto_integrate_catchup import (
    AgentPhaseActivity,
    AutoIntegrateCatchupWorker,
    process_agent_phases,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_ALREADY_CURRENT, REFRESH_ORIGIN_AHEAD
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOCAL = "a" * 40
_REMOTE = "b" * 40
_OTHER = "c" * 40


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "origin",
            }
        }
    )


@dataclass
class _RemoteDouble:
    """Remote tier double: records fetches, reports a fixed remote-tracking tip."""

    remote_sha: str | None
    refresh_outcome: str = REFRESH_ORIGIN_AHEAD
    fetches: int = 0

    def refresh_target(
        self, repo_root: Path, target: str, *, timeout_seconds: float, remote: str
    ) -> str:
        self.fetches += 1
        return self.refresh_outcome

    def push_branch(
        self, repo_root: Path, branch: str, *, remote: str, timeout_seconds: float
    ) -> PushResult:
        return PushResult(PushStatus.UNREACHABLE, remote, branch)

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        return self.remote_sha


@dataclass(frozen=True)
class _ThrottledLease:
    """The shared fetch-cadence lease, inside its window (no fetch allowed)."""

    fetch_allowed: bool = False

    def record_fetch(self) -> None:
        raise AssertionError("a throttled tick must not record a fetch")


@contextmanager
def _throttled_transaction(*_args: object, **_kwargs: object) -> Iterator[_ThrottledLease]:
    yield _ThrottledLease()


def _open_ticker_gates(
    monkeypatch: pytest.MonkeyPatch, *, ancestors: set[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Free the mutation lease and record every target advance attempt."""
    advances: list[tuple[str, str]] = []

    def _advance(
        _root: Path, target: str, sha: str, *, reclaim_target_worktree: bool
    ) -> tuple[bool, str]:
        assert reclaim_target_worktree is False
        advances.append((target, sha))
        return True, ""

    def _lease_free(_root: Path) -> bool:
        return False

    def _acquire(_root: Path, **_kwargs: object) -> object:
        return object()

    def _release(_lease: object) -> None:
        return None

    def _local_target(_root: Path, _name: str) -> tuple[str, bool]:
        return _LOCAL, True

    def _ancestor(_root: Path, older: str, newer: str) -> bool:
        return (older, newer) in ancestors

    def _primary(root: Path) -> Path:
        return root

    def _no_owner(_root: Path, _target: str) -> tuple[str, None]:
        return WORKTREE_NOT_CHECKED_OUT, None

    monkeypatch.setattr(catchup, "remote_sync_transaction", _throttled_transaction)
    monkeypatch.setattr(catchup, "is_lease_held", _lease_free)
    monkeypatch.setattr(catchup, "try_acquire_lease", _acquire)
    monkeypatch.setattr(catchup, "end_agent_lease", _release)
    monkeypatch.setattr(catchup, "observe_branch_sha", _local_target)
    monkeypatch.setattr(catchup, "is_ancestor", _ancestor)
    monkeypatch.setattr(catchup, "find_main_worktree_root", _primary)
    monkeypatch.setattr(catchup, "worktree_lookup", _no_owner)
    monkeypatch.setattr(catchup, "fast_forward_target", _advance)
    return advances


def test_throttled_tick_applies_a_strictly_ahead_tracking_ref_without_fetching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inside the fetch cadence, an ahead ``origin/main`` is still applied.

    The cached remote-tracking ref (refreshed by another seam) strictly
    descends local ``main``: the tick advances the target to it and issues no
    fetch. Before the fix the throttled tick returned before looking at it.
    """
    advances = _open_ticker_gates(monkeypatch, ancestors={(_LOCAL, _REMOTE)})
    remote = _RemoteDouble(remote_sha=_REMOTE)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        target="main",
        remote_client=remote,
        ledger=AttemptLedger(tmp_path, ledger_path=tmp_path / "ledger.json"),
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert advances == [("main", _REMOTE)]
    assert remote.fetches == 0


@pytest.mark.parametrize(
    ("cached", "ancestors"),
    [
        pytest.param(None, set(), id="no-tracking-ref"),
        pytest.param(_LOCAL, set(), id="already-equal"),
        pytest.param(_OTHER, set(), id="diverged"),
        pytest.param(_OTHER, {(_OTHER, _LOCAL)}, id="remote-behind"),
    ],
)
def test_throttled_tick_never_advances_unless_strictly_behind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cached: str | None,
    ancestors: set[tuple[str, str]],
) -> None:
    """Only a TRUE fast-forward is applied from a cached tracking ref.

    Missing, equal, diverged and behind readings leave the target alone (a
    divergence is the publication reconciler's job, never a forced move).
    """
    advances = _open_ticker_gates(monkeypatch, ancestors=ancestors)
    remote = _RemoteDouble(remote_sha=cached)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(), tmp_path, target="main", remote_client=remote
    )

    assert advances == []
    assert remote.fetches == 0
    assert outcome == catchup.CATCHUP_NOT_ON_BRANCH


def _publication_gates(
    monkeypatch: pytest.MonkeyPatch, *, advance_ok: bool
) -> list[tuple[str, str | None, bool]]:
    """Local ``main`` starts at ``_LOCAL``; a successful advance moves it to ``_REMOTE``."""
    local = {"main": _LOCAL}
    advances: list[tuple[str, str | None, bool]] = []

    def _advance(
        _root: Path,
        target: str,
        remote: str,
        *,
        reclaim_target_worktree: bool,
        remote_sha: str | None = None,
    ) -> tuple[bool, str]:
        advances.append((remote, remote_sha, reclaim_target_worktree))
        if not advance_ok:
            return False, "target owner refused merge --ff-only"
        local[target] = _REMOTE
        return True, ""

    def _catchup_tick(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("publication must not delegate to the throttled catch-up tick")

    def _branch_sha(_root: Path, name: str) -> str:
        return local[name]

    def _ancestor(_root: Path, older: str, newer: str) -> bool:
        return (older, newer) == (_LOCAL, _REMOTE)

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _ancestor)
    monkeypatch.setattr(remote_sync, "fast_forward_target_to_remote", _advance)
    monkeypatch.setattr(catchup, "attempt_catchup_fast_forward", _catchup_tick)
    return advances


def test_publication_advances_the_target_when_the_remote_is_ahead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Startup / post-tick publication with ``origin/main`` strictly ahead.

    The reconciler fast-forwards the target to the observed remote tip (no
    reclamation of a dirty owner) and re-reconciles to "already current".
    Before the fix it ran the whole catch-up tick and reported ``pull
    failed`` whenever that tick's CHECKOUT outcome was not a fast-forward.
    """
    advances = _publication_gates(monkeypatch, advance_ok=True)
    remote = _RemoteDouble(remote_sha=_REMOTE, refresh_outcome=REFRESH_ALREADY_CURRENT)

    state = remote_sync.reconcile_remote_publication(
        _config(), tmp_path, "main", remote_client=remote
    )

    assert advances == [("origin", _REMOTE, False)]
    assert state.last_remote_sync == remote_sync.REMOTE_ALREADY_CURRENT


def test_publication_reports_a_refused_advance_by_its_real_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refused target advance is ``pull failed`` with the refusal reason.

    The remote was reached and is ahead, so the record must not claim
    ``origin unreachable``; the publication stays deferred for a later seam.
    """
    _publication_gates(monkeypatch, advance_ok=False)
    remote = _RemoteDouble(remote_sha=_REMOTE, refresh_outcome=REFRESH_ALREADY_CURRENT)

    state = remote_sync.reconcile_remote_publication(
        _config(), tmp_path, "main", remote_client=remote
    )

    assert state.last_remote_sync == remote_sync.REMOTE_PULL_FAILED
    assert state.last_reason == "target owner refused merge --ff-only"
    assert state.last_refresh == REFRESH_ORIGIN_AHEAD


# ---------------------------------------------------------------------------
# Agent phases: never move the checkout an agent is working in
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DueLease:
    """The shared fetch-cadence lease with a fetch due on this tick."""

    fetch_allowed: bool = True

    def record_fetch(self) -> None:
        return None


@contextmanager
def _due_transaction(*_args: object, **_kwargs: object) -> Iterator[_DueLease]:
    yield _DueLease()


class _FakeHead:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeGit:
    def status(self, *_args: str) -> str:
        return ""


@dataclass
class _Checkouts:
    """Branch tips, the branch each checkout is on, and every move made."""

    branches: dict[str, str]
    checked_out: dict[Path, str]
    target_moves: list[str] = field(default_factory=list)
    checkout_moves: list[tuple[Path, str]] = field(default_factory=list)
    publications: int = 0


def _fake_repo_class(checkouts: _Checkouts, git_dir: Path) -> type:
    """A ``git.Repo`` stand-in: clean worktrees on the branches in ``checkouts``."""

    class _FakeRepo:
        def __init__(self, root: object, **_kwargs: object) -> None:
            self.git = _FakeGit()
            self.git_dir = str(git_dir)
            self.common_dir = str(git_dir)
            self.active_branch = _FakeHead(checkouts.checked_out[Path(str(root))])

        def close(self) -> None:
            return None

    return _FakeRepo


def _open_agent_gates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    run_checkout: Path,
    run_branch: str,
    target_owner: Path | None,
) -> _Checkouts:
    """One repository: local ``main`` at ``_LOCAL``, ``origin/main`` at ``_REMOTE``.

    ``run_checkout`` is on ``run_branch`` at ``_LOCAL`` (clean, strictly
    behind once ``main`` moves). ``target_owner`` is the checkout that has
    ``main`` checked out (``None``: no checkout). Every target advance and
    every checkout fast-forward is recorded instead of run.
    """
    checkouts = _Checkouts(
        branches={"main": _LOCAL, run_branch: _LOCAL},
        checked_out={run_checkout: run_branch},
    )
    if target_owner is not None:
        checkouts.checked_out[target_owner] = "main"

    def _sha(_root: Path, name: str) -> tuple[str | None, bool]:
        return checkouts.branches.get(name), True

    def _ancestor(_root: Path, older: str, newer: str) -> bool:
        return (older, newer) == (_LOCAL, _REMOTE)

    def _owner(_root: Path, _target: str) -> tuple[str, Path | None]:
        if target_owner is None:
            return WORKTREE_NOT_CHECKED_OUT, None
        return WORKTREE_FOUND, target_owner

    def _advance(
        _root: Path, target: str, sha: str, *, reclaim_target_worktree: bool
    ) -> tuple[bool, str]:
        assert reclaim_target_worktree is False
        checkouts.target_moves.append(sha)
        checkouts.branches[target] = sha
        return True, ""

    def _checkout_ff(root: Path, sha: str) -> bool:
        checkouts.checkout_moves.append((root, sha))
        checkouts.branches[checkouts.checked_out[root]] = sha
        return True

    def _publish(*_args: object, **_kwargs: object) -> None:
        checkouts.publications += 1

    def _lease_free(_root: Path) -> bool:
        return False

    def _acquire(_root: Path, **_kwargs: object) -> object:
        return object()

    def _release(_lease: object) -> None:
        return None

    def _primary(root: Path) -> Path:
        return root

    monkeypatch.setattr(catchup, "Repo", _fake_repo_class(checkouts, tmp_path / "no-git-dir"))
    monkeypatch.setattr(catchup, "remote_sync_transaction", _due_transaction)
    monkeypatch.setattr(catchup, "is_lease_held", _lease_free)
    monkeypatch.setattr(catchup, "try_acquire_lease", _acquire)
    monkeypatch.setattr(catchup, "end_agent_lease", _release)
    monkeypatch.setattr(catchup, "observe_branch_sha", _sha)
    monkeypatch.setattr(catchup, "is_ancestor", _ancestor)
    monkeypatch.setattr(catchup, "find_main_worktree_root", _primary)
    monkeypatch.setattr(catchup, "worktree_lookup", _owner)
    monkeypatch.setattr(catchup, "fast_forward_target", _advance)
    monkeypatch.setattr(catchup, "fast_forward_via_worktree", _checkout_ff)
    monkeypatch.setattr(remote_sync, "reconcile_remote_publication", _publish)
    return checkouts


@dataclass
class _JumpingClock:
    """A monotonic clock that jumps an hour per reading (no real waiting)."""

    current: float = 0.0

    def now(self) -> float:
        self.current += 3600.0
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


@dataclass
class _TrackingRemote(_RemoteDouble):
    """A remote whose fetch reports "already current" once ``main`` caught up."""

    checkouts: _Checkouts | None = None

    def refresh_target(
        self, repo_root: Path, target: str, *, timeout_seconds: float, remote: str
    ) -> str:
        self.fetches += 1
        assert self.checkouts is not None
        if self.checkouts.branches["main"] == self.remote_sha:
            return REFRESH_ALREADY_CURRENT
        return REFRESH_ORIGIN_AHEAD


@dataclass
class _Ticks:
    """The worker's wait seam: allow ``remaining`` ticks, then stop the loop."""

    remaining: int = 0

    def wait(self, _interval: float) -> bool:
        if self.remaining <= 0:
            return True
        self.remaining -= 1
        return False


def _worker(
    root: Path,
    phases: AgentPhaseActivity,
    remote: _RemoteDouble,
    ledger_dir: Path,
    ticks: _Ticks,
) -> AutoIntegrateCatchupWorker:
    return AutoIntegrateCatchupWorker(
        _config(),
        root,
        wait=ticks.wait,
        remote_client=remote,
        ledger=AttemptLedger(ledger_dir, ledger_path=ledger_dir / "ledger.json"),
        clock=_JumpingClock(),
        agent_phases=phases,
        stall_window_seconds=60.0,
    )


def _run(worker: AutoIntegrateCatchupWorker, ticks: _Ticks, count: int) -> None:
    """Run ``count`` real ticks through the worker's public loop."""
    ticks.remaining = count
    worker.run()


def test_agent_phase_defers_the_run_checkout_but_the_target_keeps_syncing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Feature checkout with a running agent; ``main`` checked out nowhere.

    (a) The agent's checkout is clean and strictly behind the advanced
    ``main``, yet it is NOT fast-forwarded while the phase runs. (b) The
    tick still fetched and still advanced ``main`` to ``origin/main``, and
    publication still ran (it touches no checkout of the agent's). Before
    the fix the tick fast-forwarded the agent's clean checkout mid-phase.
    """
    run_checkout = tmp_path / "wt-feature"
    checkouts = _open_agent_gates(
        monkeypatch, tmp_path, run_checkout=run_checkout, run_branch="feature", target_owner=None
    )
    phases = AgentPhaseActivity()
    remote = _RemoteDouble(remote_sha=_REMOTE)
    ledger = AttemptLedger(tmp_path, ledger_path=tmp_path / "ledger.json")

    with phases.agent_phase(run_checkout):
        outcome = catchup.attempt_catchup_fast_forward(
            _config(), run_checkout, remote_client=remote, ledger=ledger, agent_phases=phases
        )

    assert outcome == catchup.CATCHUP_DEFERRED_AGENT_PHASE
    assert checkouts.checkout_moves == []
    assert checkouts.target_moves == [_REMOTE]
    assert remote.fetches == 1


def test_first_tick_after_the_phase_lands_the_deferred_checkout_move(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(c) The worker defers during the phase, then lands the move once it ends.

    Through the worker's own loop: ticks during an hours-long phase report
    the deferral without ever raising the stall (the injected clock jumps an
    hour per reading, far past the 60 s stall window), and the first tick
    after the phase fast-forwards the checkout onto the advanced target.
    """
    run_checkout = tmp_path / "wt-feature"
    checkouts = _open_agent_gates(
        monkeypatch, tmp_path, run_checkout=run_checkout, run_branch="feature", target_owner=None
    )
    phases = AgentPhaseActivity()
    ticks = _Ticks()
    remote = _TrackingRemote(remote_sha=_REMOTE, checkouts=checkouts)
    worker = _worker(run_checkout, phases, remote, tmp_path, ticks)

    with phases.agent_phase(run_checkout):
        _run(worker, ticks, 3)
        assert worker.stall_state is None
        assert checkouts.checkout_moves == []
        assert checkouts.target_moves == [_REMOTE]
        assert remote.fetches == 3

    _run(worker, ticks, 1)

    assert checkouts.checkout_moves == [(run_checkout, _REMOTE)]
    assert worker.stall_state is None


def test_trunk_layout_agent_phase_defers_the_target_and_its_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The agent works ON ``main`` in the run checkout (trunk layout).

    During the phase the fetch still runs, but ``main`` (whose checkout is
    the agent's) is neither fast-forwarded nor touched by publication. The
    first tick after the phase advances it and publishes.
    """
    run_checkout = tmp_path / "trunk"
    checkouts = _open_agent_gates(
        monkeypatch,
        tmp_path,
        run_checkout=run_checkout,
        run_branch="main",
        target_owner=run_checkout,
    )
    phases = AgentPhaseActivity()
    ticks = _Ticks()
    remote = _TrackingRemote(remote_sha=_REMOTE, checkouts=checkouts)
    worker = _worker(run_checkout, phases, remote, tmp_path, ticks)

    with phases.agent_phase(run_checkout):
        _run(worker, ticks, 2)
        assert checkouts.target_moves == []
        assert checkouts.publications == 0
        assert remote.fetches == 2
        assert worker.stall_state is None

    _run(worker, ticks, 1)

    assert checkouts.target_moves == [_REMOTE]
    assert checkouts.publications == 1


def test_target_owned_by_another_checkout_advances_during_the_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(b) ``main`` lives in the primary clone; the agent is in its own worktree.

    The target is not the agent's checkout, so it is advanced (through its
    clean owner) during the phase; only the agent's checkout waits.
    """
    run_checkout = tmp_path / "wt-feature"
    primary = tmp_path / "clone"
    checkouts = _open_agent_gates(
        monkeypatch, tmp_path, run_checkout=run_checkout, run_branch="feature", target_owner=primary
    )
    phases = AgentPhaseActivity()
    ledger = AttemptLedger(tmp_path, ledger_path=tmp_path / "ledger.json")

    with phases.agent_phase(run_checkout):
        outcome = catchup.attempt_catchup_fast_forward(
            _config(),
            run_checkout,
            remote_client=_RemoteDouble(remote_sha=_REMOTE),
            ledger=ledger,
            agent_phases=phases,
        )

    assert outcome == catchup.CATCHUP_DEFERRED_AGENT_PHASE
    assert checkouts.target_moves == [_REMOTE]
    assert checkouts.checkout_moves == []


@pytest.mark.parametrize("failure", [RuntimeError("agent crashed"), KeyboardInterrupt()])
def test_runner_marks_the_agent_phase_for_its_whole_run_on_every_exit_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: BaseException
) -> None:
    """The runner's agent-effect branch marks the checkout, then unmarks it.

    The mark is visible to the process-wide record (the one the run's ticker
    reads) while the agent effect executes, and is gone afterwards even when
    the agent raises or the operator interrupts.
    """
    seen: list[bool] = []

    def _agent(
        _effect: object, _config: object, _deps: object, scope: object, **_kw: object
    ) -> PipelineEvent:
        assert isinstance(scope, WorkspaceScope)
        seen.append(process_agent_phases().is_active(scope.root))
        raise failure

    monkeypatch.setattr(runner_module, "execute_agent_effect", _agent)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="p.md")

    with pytest.raises(type(failure)):
        runner_module.execute_effect(
            effect, MagicMock(), WorkspaceScope(tmp_path), pipeline_deps=MagicMock()
        )

    assert seen == [True]
    assert process_agent_phases().is_active(tmp_path) is False


def test_runner_agent_phase_mark_is_released_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful agent effect leaves the checkout unmarked for the next tick."""

    def _agent(*_args: object, **_kwargs: object) -> PipelineEvent:
        assert process_agent_phases().is_active(tmp_path)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(runner_module, "execute_agent_effect", _agent)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="p.md")

    result = runner_module.execute_effect(
        effect, MagicMock(), WorkspaceScope(tmp_path), pipeline_deps=MagicMock()
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert process_agent_phases().is_active(tmp_path) is False
