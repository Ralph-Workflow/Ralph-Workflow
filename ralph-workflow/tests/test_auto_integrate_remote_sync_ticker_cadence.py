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
  and reports a refusal by its real cause rather than "unreachable".
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import WORKTREE_NOT_CHECKED_OUT
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline.auto_integrate_sync import REFRESH_ALREADY_CURRENT, REFRESH_ORIGIN_AHEAD
from ralph.pipeline.integration_attempt_ledger import AttemptLedger

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

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
def _throttled_transaction(*_args: object) -> Iterator[_ThrottledLease]:
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
