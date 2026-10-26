"""Fake-driven adversity proofs for the background catch-up ticker.

Every proof here runs against the shared simulated Git environment: no
real git, no subprocess, no sleep and no wall clock. The four properties
the ticker must hold are proven:

* (a) no worktree mutation while an agent is active, with the refusal
  naming the CURRENT observation;
* (b) repeated timeouts against one state charge the durable budget once
  per attempt and escalate a ladder rung instead of retrying forever;
* (c) a lock left by a force-killed Ralph child does not block the next
  observation, while a live or unprovable lock is never deleted;
* (d) a tick that stops progressing surfaces an accurate, visible stall
  state instead of a single WARNING line.

The catch-up collaborators are replaced through their PUBLIC seams only,
so the proofs stay black-box: the repository double answers the ref,
status and ancestry questions the tick asks of it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.pipeline import IntegrationAction
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_decision import EscalationRung
from ralph.pipeline.integration_owner import RecordFactOutcome
from tests._fake_git_env import LIVE_OWNER, STALE_OWNER, SimulatedGitEnvironment

if TYPE_CHECKING:
    from collections.abc import Iterator

_TARGET_SHA = "b" * 40
_HEAD_SHA = "a" * 40
_TARGET = "main"
_HEAD = "feature"


def _config() -> UnifiedConfig:
    """Return a config with the catch-up ticker enabled and the remote off."""
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_remote_enabled": False}}
    )


def _ledger(tmp_path: Path) -> AttemptLedger:
    """Return a durable ledger rooted in the temporary workspace."""
    return AttemptLedger(tmp_path, ledger_path=tmp_path / ".agent" / "catchup-ledger.json")


class _FakeCommit:
    """Commit double exposing the tip SHA the tick reads."""

    def __init__(self, sha: str) -> None:
        self.hexsha = sha


class _FakeHead:
    """Branch double exposing the name and tip the tick observes."""

    def __init__(self, name: str, sha: str) -> None:
        self.name = name
        self.commit = _FakeCommit(sha)


class _FakeBranch:
    """Branch double for the attached branch the tick reads."""

    name = _HEAD


class _FakeGit:
    """GitPython's dynamic dispatch double for the porcelain status probe."""

    def status(self, *_args: object, **_kwargs: object) -> str:
        """Return an empty porcelain status: the tracked worktree is clean."""
        return ""


class _FakeRepo:
    """Repository double answering every read the catch-up tick performs."""

    def __init__(self, root: Path, *_args: object, **_kwargs: object) -> None:
        self.git_dir = root / ".git"
        self.common_dir = root / ".git"
        self.active_branch = _FakeBranch()
        self.git = _FakeGit()
        self.heads = [_FakeHead(_TARGET, _TARGET_SHA), _FakeHead(_HEAD, _HEAD_SHA)]

    def commit(self, sha: str) -> _FakeCommit:
        """Return the commit double for the given SHA."""
        return _FakeCommit(sha)

    def is_ancestor(self, _ancestor: _FakeCommit, _descendant: _FakeCommit) -> bool:
        """Report the target as a descendant: the checkout is strictly behind."""
        return True

    def close(self) -> None:
        """Match GitPython's close contract."""


class _LockEnvironment:
    """Maps the simulated lock ownership onto the catch-up liveness seam."""

    def __init__(self, state: SimulatedGitEnvironment) -> None:
        self._state = state

    def candidates(self, _git_dir: Path, _common_dir: Path) -> list[Path]:
        """Return every lock the simulated repository currently holds."""
        return [Path(name) for name in self._state.locks]

    def verdict(self, lock_path: Path, _git_dir: Path, _common_dir: Path) -> str:
        """Return the liveness verdict the probe would reach for the lock."""
        owner = self._state.locks.get(lock_path.name)
        if owner == STALE_OWNER:
            return "dead"
        if owner == LIVE_OWNER:
            return "live"
        return "unknown"

    def remove(self, lock_path: Path, _identity: tuple[int, int, int] | None) -> bool:
        """Remove the lock and report the removal to the simulated state."""
        self._state.locks.pop(lock_path.name, None)
        return True


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    state: SimulatedGitEnvironment,
    *,
    merge_fault: BaseException | None = None,
) -> list[str]:
    """Patch every catch-up collaborator; return the recorded merge calls."""
    merges: list[str] = []
    locks = _LockEnvironment(state)

    def _merge(_root: Path, sha: str) -> bool:
        merges.append(sha)
        if merge_fault is not None:
            raise merge_fault
        return True

    def _identity(_lock_path: Path) -> tuple[int, int, int]:
        return (1, 2, 3)

    monkeypatch.setattr(catchup, "Repo", _FakeRepo)
    monkeypatch.setattr(catchup, "fast_forward_via_worktree", _merge)
    monkeypatch.setattr(catchup, "is_lease_held", lambda _root: state.agent_active)
    monkeypatch.setattr(
        catchup,
        "try_acquire_lease",
        lambda *_args, **_kwargs: None if state.agent_active else object(),
    )
    monkeypatch.setattr(catchup, "end_agent_lease", lambda _lease: None)
    monkeypatch.setattr(catchup, "git_lock_candidates", locks.candidates)
    monkeypatch.setattr(catchup, "lock_has_no_live_holder", locks.verdict)
    monkeypatch.setattr(catchup, "lock_identity", _identity)
    monkeypatch.setattr(catchup, "unlink_if_unchanged", locks.remove)
    return merges


def _capture_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the ticker's warnings as formatted text."""
    messages: list[str] = []

    def _record(message: str, *args: object, **_kwargs: object) -> None:
        messages.append(message.format(*args) if args else message)

    monkeypatch.setattr(logger, "warning", _record)
    return messages


def _tick(state: SimulatedGitEnvironment, ledger: AttemptLedger, root: Path) -> str:
    """Run one catch-up tick against the simulated repository."""
    return catchup.attempt_catchup_fast_forward(
        _config(),
        root,
        target=_TARGET,
        ledger=ledger,
        clock=state.clock,
    )


def test_agent_active_refuses_without_mutation_and_names_the_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(a) An active agent blocks the tick and the refusal names the state."""
    state = SimulatedGitEnvironment()
    state.agent_active = True
    merges = _install_seams(monkeypatch, state)
    warnings = _capture_warnings(monkeypatch)
    ledger = _ledger(tmp_path)

    outcome = _tick(state, ledger, tmp_path)

    assert outcome == catchup.CATCHUP_REFUSED_AGENT_ACTIVE
    assert merges == []
    assert ledger.read().repeat_counts == {}
    deferred = [message for message in warnings if "deferred" in message]
    assert deferred, warnings
    assert f"target={_TARGET}@{_TARGET_SHA}" in deferred[0]
    assert f"head={_HEAD}@{_HEAD_SHA}" in deferred[0]


def test_repeated_timeouts_charge_the_budget_once_per_attempt_then_escalate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(b) The same timeout charges the budget once and then climbs the ladder."""
    state = SimulatedGitEnvironment()
    merges = _install_seams(
        monkeypatch,
        state,
        merge_fault=subprocess.TimeoutExpired(["git", "merge"], 120.0),
    )
    ledger = _ledger(tmp_path)

    outcomes = [_tick(state, ledger, tmp_path) for _ in range(3)]

    assert len(merges) == 2, "the identical merge must not be retried a third time"
    assert all(catchup.CATCHUP_TIMEOUT in outcome for outcome in outcomes[:2])
    assert outcomes[2].startswith(catchup.CATCHUP_LADDER_ESCALATED)
    assert f"target={_TARGET}@{_TARGET_SHA}" in outcomes[2]
    durable = ledger.read()
    assert sum(durable.repeat_counts.values()) == 2
    assert durable.rung is EscalationRung.DISPATCH_RESOLVER


def test_stale_lock_from_a_killed_child_is_recovered_before_the_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(c) A lock proven owner-dead is removed and never blocks the tick."""
    state = SimulatedGitEnvironment()
    state.leave_stale_lock("index.lock")
    merges = _install_seams(monkeypatch, state)

    outcome = _tick(state, _ledger(tmp_path), tmp_path)

    assert "index.lock" not in state.locks
    assert merges == [_TARGET_SHA]
    assert outcome == catchup.CATCHUP_FAST_FORWARDED


@pytest.mark.parametrize(
    ("owner", "expected"),
    [(LIVE_OWNER, catchup.CATCHUP_LOCK_HELD), ("uncertain", catchup.CATCHUP_LOCK_UNKNOWN)],
)
def test_live_or_uncertain_lock_is_kept_and_the_tick_backs_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    owner: str,
    expected: str,
) -> None:
    """(c) A lock that cannot be proven free is kept and the tick backs off."""
    state = SimulatedGitEnvironment()
    state.locks["index.lock"] = owner
    merges = _install_seams(monkeypatch, state)
    before = state.clock.now()

    outcome = _tick(state, _ledger(tmp_path), tmp_path)

    assert "index.lock" in state.locks
    assert merges == []
    assert outcome.startswith(expected)
    assert "index.lock" in outcome
    assert state.clock.now() > before


def test_no_progress_window_surfaces_a_visible_stall_state(tmp_path: Path) -> None:
    """(d) A ticker that stops progressing reports an accurate stall state."""
    state = SimulatedGitEnvironment()
    outcome = f"{catchup.CATCHUP_TIMEOUT}; target={_TARGET}@{_TARGET_SHA}; head={_HEAD}@{_HEAD_SHA}"
    waits: Iterator[bool] = iter((False, True))

    def _wait(_interval: float) -> bool:
        state.clock.sleep(6.0)
        return next(waits)

    worker = catchup.AutoIntegrateCatchupWorker(
        _config(),
        tmp_path,
        tick=lambda: outcome,
        wait=_wait,
        clock=state.clock,
        stall_window_seconds=5.0,
    )

    worker.run()

    stall = worker.stall_state
    assert stall is not None
    assert stall.cause == outcome
    assert stall.elapsed_seconds >= stall.window_seconds


# ---------------------------------------------------------------------------
# The tick asks the single owner through the completed seam (remote + record).
# ---------------------------------------------------------------------------


def _spy_seam(
    monkeypatch: pytest.MonkeyPatch,
    verdict: IntegrationAction | RecordFactOutcome,
) -> list[dict[str, object]]:
    """Record the bindings the tick hands the owner seam; answer ``verdict``."""
    calls: list[dict[str, object]] = []

    def _spy(root: Path, **kwargs: object) -> IntegrationAction | RecordFactOutcome:
        calls.append({"root": root, **kwargs})
        return verdict

    monkeypatch.setattr(catchup, "decide_seam_action", _spy)
    return calls


def test_tick_asks_the_owner_seam_with_the_remote_and_the_loaded_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The completed seam call carries ``remote`` and the durable ``record``.

    The catch-up ticker holds no private view of the durable record: it
    loads the record from disk and hands it, together with the configured
    remote, to the single owner — the same facts every other seam supplies.
    """
    state = SimulatedGitEnvironment()
    merges = _install_seams(monkeypatch, state)
    calls = _spy_seam(monkeypatch, IntegrationAction.WAIT_FOR_BOUNDED_RETRY)
    (tmp_path / ".git").mkdir()
    record = IntegrationRecord(
        phase="integrating",
        target=_TARGET,
        pre_feature_sha=_HEAD_SHA,
        pre_target_sha=_TARGET_SHA,
    )
    write_record(tmp_path, record)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        target=_TARGET,
        remote="upstream",
        ledger=_ledger(tmp_path),
        clock=state.clock,
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert merges == [_TARGET_SHA]
    assert len(calls) == 1
    seam_kwargs = calls[0]
    assert seam_kwargs["root"] == tmp_path
    assert seam_kwargs["remote"] == "upstream"
    assert seam_kwargs["target"] == _TARGET
    # The mutation lease is held, so the observation records no active agent.
    assert seam_kwargs["agent_active"] is False
    passed_record = seam_kwargs["record"]
    assert isinstance(passed_record, IntegrationRecord)
    assert passed_record.target == _TARGET


def test_tick_seam_remote_falls_back_to_the_configured_remote_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without an explicit ``remote`` the seam observes the configured name."""
    state = SimulatedGitEnvironment()
    _install_seams(monkeypatch, state)
    calls = _spy_seam(monkeypatch, IntegrationAction.WAIT_FOR_BOUNDED_RETRY)
    (tmp_path / ".git").mkdir()
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_remote_enabled": False,
                "auto_integrate_remote": "fork",
            }
        }
    )

    outcome = catchup.attempt_catchup_fast_forward(
        config,
        tmp_path,
        target=_TARGET,
        ledger=_ledger(tmp_path),
        clock=state.clock,
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert len(calls) == 1
    assert calls[0]["remote"] == "fork"


def test_owner_seam_refusal_blocks_the_merge_without_a_ledger_charge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fail-closed seam verdict refuses the mutation before any charge.

    The typed STALE_INTENT verdict escalates through the autonomous
    cleanup ladder — the catch-up mutation defers to it, never to a
    person.
    """
    state = SimulatedGitEnvironment()
    merges = _install_seams(monkeypatch, state)
    _spy_seam(monkeypatch, RecordFactOutcome.STALE_INTENT)
    (tmp_path / ".git").mkdir()
    ledger = _ledger(tmp_path)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        target=_TARGET,
        ledger=ledger,
        clock=state.clock,
    )

    assert merges == []
    assert outcome.startswith(catchup.CATCHUP_REFUSED)
    assert RecordFactOutcome.STALE_INTENT.value in outcome
    assert ledger.read().repeat_counts == {}


# ---------------------------------------------------------------------------
# (e) A broken attempt ledger fails closed: the mutation is refused with a
# typed reason, the failure is counted against a bounded budget, and the
# refusal escalates through the ladder path once the bound trips. The tick
# itself never stops running and never proceeds unledgered.
# ---------------------------------------------------------------------------


class _BrokenLedger(AttemptLedger):
    """Ledger double whose read/charge calls raise, like a broken store."""

    def __init__(self, fault: BaseException) -> None:
        super().__init__(Path("/nonexistent-workspace"))
        self._fault = fault

    def read(self) -> AttemptLedgerState:
        raise self._fault

    def charge(self, signature: str, action: str, *, is_unknown: bool) -> AttemptLedgerState:
        raise self._fault


class _SwitchableLedger(AttemptLedger):
    """Ledger double that delegates to a real ledger unless marked broken."""

    def __init__(self, root: Path, fault: BaseException) -> None:
        super().__init__(root, ledger_path=root / ".agent" / "switchable-ledger.json")
        self._fault = fault
        self.broken = False

    def read(self) -> AttemptLedgerState:
        if self.broken:
            raise self._fault
        return super().read()

    def charge(self, signature: str, action: str, *, is_unknown: bool) -> AttemptLedgerState:
        if self.broken:
            raise self._fault
        return super().charge(signature, action, is_unknown=is_unknown)


def _tick_with_budget(
    state: SimulatedGitEnvironment,
    ledger: AttemptLedger,
    root: Path,
    budget: catchup.CatchupLedgerFailureBudget,
) -> str:
    """Run one catch-up tick with a shared ledger-failure budget."""
    return catchup.attempt_catchup_fast_forward(
        _config(),
        root,
        target=_TARGET,
        ledger=ledger,
        clock=state.clock,
        ledger_failure_budget=budget,
    )


def test_ledger_read_failure_refuses_the_mutation_with_a_typed_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(e) One ledger failure refuses the mutation, named, without merging."""
    state = SimulatedGitEnvironment()
    merges = _install_seams(monkeypatch, state)
    ledger = _BrokenLedger(OSError("ledger store gone"))
    budget = catchup.CatchupLedgerFailureBudget()
    before = state.clock.now()

    outcome = _tick_with_budget(state, ledger, tmp_path, budget)

    assert outcome.startswith(catchup.CATCHUP_LEDGER_UNAVAILABLE)
    assert f"target={_TARGET}@{_TARGET_SHA}" in outcome
    assert merges == [], "a ledger failure must never proceed unledgered"
    assert state.clock.now() > before, "the failure must cost a bounded backoff"
    assert budget.consecutive_failures == 1


def test_ledger_failures_escalate_to_the_ladder_once_the_bound_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(e) Repeated ledger failures escalate instead of retrying forever."""
    state = SimulatedGitEnvironment()
    merges = _install_seams(monkeypatch, state)
    ledger = _BrokenLedger(OSError("ledger store gone"))
    budget = catchup.CatchupLedgerFailureBudget()

    first = _tick_with_budget(state, ledger, tmp_path, budget)
    second = _tick_with_budget(state, ledger, tmp_path, budget)

    assert first.startswith(catchup.CATCHUP_LEDGER_UNAVAILABLE)
    assert second.startswith(catchup.CATCHUP_LADDER_ESCALATED)
    assert catchup.CATCHUP_LEDGER_UNAVAILABLE in second
    assert f"target={_TARGET}@{_TARGET_SHA}" in second
    assert merges == []
    assert budget.consecutive_failures == 2


def test_ledger_failures_reset_after_a_successful_charge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(e) A healed ledger resets the failure budget instead of accumulating."""
    state = SimulatedGitEnvironment()
    merges = _install_seams(monkeypatch, state)
    ledger = _SwitchableLedger(tmp_path, OSError("ledger store gone"))
    budget = catchup.CatchupLedgerFailureBudget()

    ledger.broken = True
    first = _tick_with_budget(state, ledger, tmp_path, budget)
    ledger.broken = False
    healed = _tick_with_budget(state, ledger, tmp_path, budget)
    ledger.broken = True
    third = _tick_with_budget(state, ledger, tmp_path, budget)

    assert first.startswith(catchup.CATCHUP_LEDGER_UNAVAILABLE)
    assert healed == catchup.CATCHUP_FAST_FORWARDED
    assert merges == [_TARGET_SHA]
    assert third.startswith(catchup.CATCHUP_LEDGER_UNAVAILABLE)
    assert "attempts=1" in third, "a healed ledger must reset the failure count"


def test_a_ledger_outage_never_stops_the_catchup_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(e) The worker keeps ticking through a ledger outage and names it."""
    state = SimulatedGitEnvironment()
    merges = _install_seams(monkeypatch, state)
    warnings = _capture_warnings(monkeypatch)
    ledger = _BrokenLedger(OSError("ledger store gone"))
    waits: Iterator[bool] = iter((False, False, True))

    worker = catchup.AutoIntegrateCatchupWorker(
        _config(),
        tmp_path,
        ledger=ledger,
        clock=state.clock,
        wait=lambda _interval: next(waits),
    )

    worker.run()

    named = [message for message in warnings if "tick failed" in message]
    assert len(named) == 2, "both ticks ran: the outage must never stop the run"
    assert any(catchup.CATCHUP_LEDGER_UNAVAILABLE in message for message in named)
    assert any(
        message.startswith("auto_integrate catch-up tick failed: ladder:") for message in named
    )
    assert merges == []


def test_a_settled_tick_never_reports_a_stall(tmp_path: Path) -> None:
    """(d) A healthy ticker stays silent: settled outcomes reset the clock."""
    state = SimulatedGitEnvironment()
    waits: Iterator[bool] = iter((False, True))

    def _wait(_interval: float) -> bool:
        state.clock.sleep(600.0)
        return next(waits)

    worker = catchup.AutoIntegrateCatchupWorker(
        _config(),
        tmp_path,
        tick=lambda: catchup.CATCHUP_UP_TO_DATE,
        wait=_wait,
        clock=state.clock,
        stall_window_seconds=5.0,
    )

    worker.run()

    assert worker.stall_state is None
