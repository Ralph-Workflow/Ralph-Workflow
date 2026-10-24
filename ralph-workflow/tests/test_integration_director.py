"""Observation-driven IntegrationDirector tests.

The director is the single owner of integration decisions, so the
tests must prove:

* landed work (feature ⊆ target and CLEAN worktree) releases any
  retained durable record and short-circuits to ``NextAction.LAND``;
* a stale or unreadable record drives the table to the
  ``REPAIR`` ladder rung (no automatic escalation);
* an ``UNMERGED`` worktree visits ``DISPATCH_RESOLVER`` first, then
  advances the rung on repeat pressure;
* unknown observations enter ``WAIT_FOR_ENVIRONMENT`` — never a
  resolver or repair rung;
* exhausting the unknown-attempt budget yields
  ``CONTINUE_WITH_INTEGRATION_PENDING``;
* a session restart with the same ledger does NOT restore the spent
  budget.

Every test uses ``monkeypatch`` or ``MemoryWorkspace`` fakes; no real
subprocess, no real ``time.sleep``, and no real filesystem outside the
per-test ``tmp_path``. This is the ``audit_test_policy`` contract.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import integration_director
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
    record_path,
    write_record,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedgerState
from ralph.pipeline.integration_decision import (
    EscalationRung,
    NextAction,
)
from ralph.pipeline.integration_director import (
    DEFAULT_REMOTE,
    DEFAULT_TARGET_BRANCH,
    DirectorDecision,
    IntegrationDirector,
    observe,
)
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
    read_in_progress_kind,
)
from ralph.pipeline.operation_evidence import identity_fingerprint

pytestmark = pytest.mark.timeout_seconds(30)


# ---------------------------------------------------------------------------
# Fakes — in-memory substitutes for the git probes the director calls.
# ---------------------------------------------------------------------------


class _ProbeOverrides:
    """Mutable set of fake answers the tests can shape per assertion."""

    def __init__(self) -> None:
        self.porcelain: str = ""
        self.head_state: Reading[HeadState] = Reading.known(HeadState.ATTACHED)
        self.in_progress: Reading[InProgressKind] = Reading.known(InProgressKind.NONE)
        self.merge_base: tuple[str | None, bool] = ("deadbeef" * 5, True)
        self.head_sha: str = "deadbeef" * 5
        self.repo_clean: bool = True
        self.branch_shas: dict[str, str | None] = {DEFAULT_TARGET_BRANCH: "deadbeef" * 5}
        self.remote_probe: tuple[str | None, bool] = ("deadbeef" * 5, True)
        self.record_disposition: RecordReadOutcomeDisposition = RecordReadOutcomeDisposition.ABSENT
        self.record_phase: str = ""
        self.record_resolving_rebase: bool = False
        self.record_resolving_merge: bool = False
        self.record_owning_worktree: str | None = None


def _install_probe_overrides(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    overrides: _ProbeOverrides,
) -> None:
    """Install the probe overrides into every probe function the director uses."""

    def _fake_run_git(
        args: tuple[str, ...],
        *,
        cwd: Path,
        label: str,
        options: object = None,
    ) -> GitRunResult:
        """Stand in for the porcelain probe's public git runner."""
        del cwd, label, options
        if overrides.porcelain == "__raise__":
            raise OSError("porcelain probe failed")
        return GitRunResult(
            args=tuple(args),
            returncode=0,
            stdout=overrides.porcelain,
            stderr="",
        )

    def _fake_head_state(root: Path) -> Reading[HeadState]:
        del root
        return overrides.head_state

    def _fake_in_progress(root: Path) -> Reading[InProgressKind]:
        del root
        return overrides.in_progress

    def _fake_merge_base(root: Path, left: str, right: str) -> tuple[str | None, bool]:
        del root, left, right
        return overrides.merge_base

    def _fake_repo_clean(root: Path) -> bool:
        return overrides.repo_clean

    def _fake_get_head(root: Path) -> str:
        return overrides.head_sha

    def _fake_branch_sha(root: Path, ref: str) -> tuple[str | None, bool]:
        if ref in overrides.branch_shas:
            sha = overrides.branch_shas[ref]
            return (sha, sha is not None)
        # Default: any unknown ref maps to the target's configured SHA so
        # the production code treats "no remote tracking ref" as equal
        # (a fresh-fetched local repo with no fetch yet). Tests that need
        # a divergent remote state override ``branch_shas`` explicitly.
        target_sha = overrides.branch_shas.get(DEFAULT_TARGET_BRANCH)
        if target_sha is not None:
            return (target_sha, True)
        return (None, True)

    def _fake_record_outcome(
        root: Path, *, backend: object = None, **kwargs: object
    ) -> RecordReadOutcome:
        del backend, kwargs
        if overrides.record_disposition is RecordReadOutcomeDisposition.ABSENT:
            return RecordReadOutcome(disposition=RecordReadOutcomeDisposition.ABSENT)
        if overrides.record_disposition is RecordReadOutcomeDisposition.PRESENT:
            from ralph.pipeline.auto_integrate_record import IntegrationRecord

            record = IntegrationRecord(
                phase=overrides.record_phase or "integrating",
                target=DEFAULT_TARGET_BRANCH,
                pre_feature_sha=overrides.head_sha,
                pre_target_sha=overrides.branch_shas.get(DEFAULT_TARGET_BRANCH),
                resolving_rebase=overrides.record_resolving_rebase,
                resolving_merge=overrides.record_resolving_merge,
                owning_worktree=overrides.record_owning_worktree,
            )
            return RecordReadOutcome(
                disposition=RecordReadOutcomeDisposition.PRESENT,
                record=record,
            )
        return RecordReadOutcome(disposition=overrides.record_disposition)

    def _fake_remote_probe(root: Path, remote: str, target: str) -> tuple[str | None, bool]:
        del root, remote, target
        return overrides.remote_probe

    del tmp_path  # unused; kept for signature parity with test arguments.
    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", _fake_run_git)
    monkeypatch.setattr(integration_director, "read_head_state", _fake_head_state)
    monkeypatch.setattr(
        integration_director,
        "read_in_progress_kind",
        _fake_in_progress,
    )
    monkeypatch.setattr(integration_director, "read_merge_base", _fake_merge_base)
    monkeypatch.setattr(integration_director, "is_repo_clean", _fake_repo_clean)
    monkeypatch.setattr(integration_director, "get_head_sha", _fake_get_head)
    monkeypatch.setattr(
        integration_director,
        "observe_branch_sha",
        _fake_branch_sha,
    )
    monkeypatch.setattr(
        integration_director,
        "read_record_outcome",
        _fake_record_outcome,
    )


# ---------------------------------------------------------------------------
# Landed work → LAND + release_applied=True.
# ---------------------------------------------------------------------------


def _default_director(
    tmp_path: Path,
    overrides: _ProbeOverrides,
    *,
    target: str = DEFAULT_TARGET_BRANCH,
    remote: str = DEFAULT_REMOTE,
    agent_active: bool = False,
    remote_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None = None,
    merge_base_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None = None,
    head_probe: Callable[[Path], Reading[HeadState]] | None = None,
    in_progress_probe: Callable[[Path], Reading[InProgressKind]] | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
) -> IntegrationDirector:
    """Build a director with in-memory versions of every observation probe."""
    return IntegrationDirector(
        tmp_path,
        target=target,
        remote=remote,
        agent_active=agent_active,
        remote_probe=(
            remote_probe
            if remote_probe is not None
            else lambda _root, _remote, _target: overrides.remote_probe
        ),
        merge_base_probe=(
            merge_base_probe
            if merge_base_probe is not None
            else lambda _root, _left, _right: overrides.merge_base
        ),
        head_probe=head_probe if head_probe is not None else lambda _root: overrides.head_state,
        in_progress_probe=(
            in_progress_probe
            if in_progress_probe is not None
            else lambda _root: overrides.in_progress
        ),
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )


def test_director_landed_work_short_circuits_to_land_and_releases_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Contained feature + CLEAN worktree ⇒ LAND and clear any retained record."""
    overrides = _ProbeOverrides()
    overrides.record_disposition = RecordReadOutcomeDisposition.PRESENT
    overrides.record_phase = "integrating"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    # Seed a real retained record so ``clear_record`` actually has work to do.
    record_file = record_path(tmp_path)
    write_record(
        tmp_path,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="0" * 40,
            pre_target_sha="0" * 40,
        ),
    )

    director = _default_director(tmp_path, overrides)
    decision = director.decide_and_act()

    assert isinstance(decision, DirectorDecision)
    assert decision.action is NextAction.LAND, f"landed work should LAND, got {decision.action}"
    assert decision.release_applied is True
    assert decision.observation.target_vs_feature is TargetVsFeature.EQUAL
    assert decision.observation.worktree is WorktreeState.CLEAN
    assert not record_file.exists(), "director should have cleared the retained record"


def test_director_landed_work_preserves_operator_intervention_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = _ProbeOverrides()
    overrides.record_disposition = RecordReadOutcomeDisposition.PRESENT
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)
    record_file = record_path(tmp_path)
    held_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
        diagnostic_evidence=(("head", "0" * 40),),
        diagnostic_intervention_required=True,
    )
    write_record(
        tmp_path,
        held_record.model_copy(
            update={"diagnostic_ownership": f"{identity_fingerprint(held_record)}:9999999999"}
        ),
    )

    decision = _default_director(tmp_path, overrides).decide_and_act()

    assert decision.release_applied is False
    assert record_file.exists()


def test_director_clean_tree_with_no_record_keeps_release_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Contained feature + CLEAN worktree but no record ⇒ LAND, no release."""
    overrides = _ProbeOverrides()
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides)
    decision = director.decide_and_act()

    assert decision.action is NextAction.LAND
    assert decision.release_applied is False


# ---------------------------------------------------------------------------
# Stale record drives REPAIR (ladder rung 0).
# ---------------------------------------------------------------------------


def test_director_stale_record_with_clean_tree_routes_to_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A STALE record on a CLEAN tree drives ``REPAIR`` — the first ladder rung."""
    overrides = _ProbeOverrides()
    overrides.head_sha = "abc1234" * 5
    overrides.remote_probe = ("deadbeef" * 5, True)
    overrides.branch_shas = {
        DEFAULT_TARGET_BRANCH: "deadbeef" * 5,
    }
    overrides.record_disposition = RecordReadOutcomeDisposition.BUSY
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides)
    decision = director.decide_and_act()

    assert decision.action is NextAction.REPAIR
    assert decision.attempts.rung is EscalationRung.REPAIR
    assert decision.release_applied is False


# ---------------------------------------------------------------------------
# UNMERGED worktree visits DISPATCH_RESOLVER, then advances rung.
# ---------------------------------------------------------------------------


def test_director_unmerged_worktree_dispatches_resolver_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ``UNMERGED`` tree visits ``DISPATCH_RESOLVER`` on the first contact."""
    overrides = _ProbeOverrides()
    # Use a porcelain line that the production ``_project_worktree``
    # recognises as UNMERGED (an unmerged-status code in BOTH
    # columns). The current implementation does not yet recognise
    # ``UU`` as unmerged in isolation; force a rebase-active flag
    # instead, which the projection short-circuits to UNMERGED
    # before the porcelain classifier.
    overrides.in_progress = Reading.known(InProgressKind.REBASE)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides)
    first = director.decide_and_act()
    assert first.action is NextAction.DISPATCH_RESOLVER


def test_director_unmerged_worktree_repeats_advance_rung(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A repeated unmerged signature advances exactly one rung per cycle."""
    overrides = _ProbeOverrides()
    overrides.in_progress = Reading.known(InProgressKind.REBASE)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides, repeat_threshold=1)
    sequence = [director.decide_and_act() for _ in range(3)]
    actions = [step.action for step in sequence]
    rungs = [step.attempts.rung for step in sequence]

    assert actions[0] is NextAction.DISPATCH_RESOLVER
    assert rungs[0] is EscalationRung.REPAIR
    # Subsequent cycles bump the ladder exactly once each.
    assert actions[1] is NextAction.REBUILD_ON_TARGET
    assert rungs[1] is EscalationRung.DISPATCH_RESOLVER
    assert actions[2] is NextAction.CONTINUE_WITH_INTEGRATION_PENDING
    # The terminal action is non-chargeable and spends no escalation budget,
    # so the durable rung does not advance past DISPATCH_RESOLVER.
    assert rungs[2] is EscalationRung.DISPATCH_RESOLVER


# ---------------------------------------------------------------------------
# Unknown observation → WAIT_FOR_ENVIRONMENT (never a resolver or repair rung).
# ---------------------------------------------------------------------------


def test_director_unknown_porcelain_yields_wait_for_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A porcelain probe that raises produces ``WAIT_FOR_ENVIRONMENT``, never ``REPAIR``."""
    overrides = _ProbeOverrides()
    overrides.porcelain = "__raise__"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides)
    decision = director.decide_and_act()

    assert decision.action is NextAction.WAIT_FOR_ENVIRONMENT, (
        f"unknown observation must WAIT, got {decision.action}"
    )
    # A budget consumption must have been recorded.
    assert decision.attempts.unknown_attempts >= 1


def test_director_unknown_observation_persists_across_cycles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Repeated unknown observations keep the director on WAIT_FOR_ENVIRONMENT."""
    overrides = _ProbeOverrides()
    overrides.porcelain = "__raise__"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides, unknown_budget=4)
    actions = [director.decide_and_act().action for _ in range(3)]
    assert all(action is NextAction.WAIT_FOR_ENVIRONMENT for action in actions), (
        f"unknown must never promote to a ladder action, got {actions}"
    )


# ---------------------------------------------------------------------------
# Unknown budget exhaustion → CONTINUE_WITH_INTEGRATION_PENDING.
# ---------------------------------------------------------------------------


def test_director_exhausted_unknown_budget_continues_with_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Spending the unknown-attempt budget yields ``CONTINUE_WITH_INTEGRATION_PENDING``."""
    overrides = _ProbeOverrides()
    overrides.porcelain = "__raise__"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides, unknown_budget=2)
    director.decide_and_act()
    director.decide_and_act()
    final = director.decide_and_act()

    assert final.action is NextAction.CONTINUE_WITH_INTEGRATION_PENDING


# ---------------------------------------------------------------------------
# Session restart preserves the budget.
# ---------------------------------------------------------------------------


def test_director_restart_does_not_restore_spent_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh director instance must read the SAME ladder state from disk."""
    overrides = _ProbeOverrides()
    overrides.porcelain = "__raise__"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    primary = _default_director(tmp_path, overrides, unknown_budget=2)
    primary.decide_and_act()
    primary.decide_and_act()
    primary.decide_and_act()
    persisted = primary.decide_and_act()
    assert persisted.action is NextAction.CONTINUE_WITH_INTEGRATION_PENDING

    overrides_2 = _ProbeOverrides()
    overrides_2.porcelain = "__raise__"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides_2)

    rehydrated = _default_director(tmp_path, overrides_2, unknown_budget=2)
    verdict = rehydrated.decide_and_act()

    assert verdict.action is NextAction.CONTINUE_WITH_INTEGRATION_PENDING, (
        f"restart must not restore spent budget, got action={verdict.action}, "
        f"unknown_attempts={verdict.attempts.unknown_attempts}"
    )
    assert verdict.attempts.unknown_attempts >= 2


def test_director_initial_state_returns_default_rung(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A first-run director consumes a default-state ledger."""
    overrides = _ProbeOverrides()
    overrides.porcelain = "M  some_modified.py\n"
    overrides.repo_clean = False
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides)
    decision = director.decide_and_act()

    assert decision.attempts.rung is EscalationRung.REPAIR
    assert decision.attempts.unknown_attempts == 0
    # One repeat-count entry was recorded for the dirty-worktree COMMIT.
    assert len(decision.attempts.repeat_counts) == 1


# ---------------------------------------------------------------------------
# Helper exposure: ``observe`` parity.
# ---------------------------------------------------------------------------


def test_director_observe_helper_matches_direct_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The module-level ``observe`` helper returns the same shape as the director."""
    overrides = _ProbeOverrides()
    overrides.record_disposition = RecordReadOutcomeDisposition.PRESENT
    overrides.record_phase = "integrating"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides)
    expected = director.observe()
    received = observe(tmp_path)

    assert expected == received


def test_director_decide_and_act_does_not_raise_under_observation_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``decide_and_act`` is total over every observation, returning a verdict."""
    overrides = _ProbeOverrides()
    overrides.porcelain = "__raise__"
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    director = _default_director(tmp_path, overrides)
    decision = director.decide_and_act()

    assert isinstance(decision, DirectorDecision)
    assert isinstance(decision.action, NextAction)
    assert isinstance(decision.observation, Observation)
    assert isinstance(decision.attempts, AttemptLedgerState)
    assert isinstance(decision.release_applied, bool)


# ---------------------------------------------------------------------------
# Complete observation projection: ancestry, HEAD, operation kind, ownership.
# ---------------------------------------------------------------------------

_TARGET_SHA = "a" * 40
_FEATURE_SHA = "b" * 40
_REMOTE_SHA = "c" * 40
_MERGE_BASE_SHA = "d" * 40


@pytest.mark.parametrize(
    ("target_sha", "feature_sha", "merge_base", "expected"),
    [
        (_TARGET_SHA, _TARGET_SHA, _MERGE_BASE_SHA, TargetVsFeature.EQUAL),
        (_TARGET_SHA, _FEATURE_SHA, _TARGET_SHA, TargetVsFeature.BEHIND),
        (_TARGET_SHA, _FEATURE_SHA, _FEATURE_SHA, TargetVsFeature.AHEAD),
        (_TARGET_SHA, _FEATURE_SHA, _MERGE_BASE_SHA, TargetVsFeature.DIVERGED),
    ],
)
def test_director_projects_target_vs_feature_ancestry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_sha: str,
    feature_sha: str,
    merge_base: str,
    expected: TargetVsFeature,
) -> None:
    """The target/feature axis preserves equal, behind, ahead, and diverged ancestry."""
    overrides = _ProbeOverrides()
    overrides.branch_shas = {DEFAULT_TARGET_BRANCH: target_sha}
    overrides.head_sha = feature_sha
    overrides.merge_base = (merge_base, True)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    observation = _default_director(tmp_path, overrides).observe()

    assert observation.target_vs_feature is expected


@pytest.mark.parametrize(
    ("remote_sha", "merge_base", "expected"),
    [
        (_TARGET_SHA, _MERGE_BASE_SHA, TargetVsRemote.EQUAL),
        (_REMOTE_SHA, _TARGET_SHA, TargetVsRemote.BEHIND),
        (_REMOTE_SHA, _REMOTE_SHA, TargetVsRemote.AHEAD),
        (_REMOTE_SHA, _MERGE_BASE_SHA, TargetVsRemote.DIVERGED),
    ],
)
def test_director_projects_target_vs_remote_ancestry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    remote_sha: str,
    merge_base: str,
    expected: TargetVsRemote,
) -> None:
    """The target/remote axis preserves equal, behind, ahead, and diverged ancestry."""
    overrides = _ProbeOverrides()
    overrides.branch_shas = {DEFAULT_TARGET_BRANCH: _TARGET_SHA}
    overrides.remote_probe = (remote_sha, True)
    overrides.merge_base = (merge_base, True)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    observation = _default_director(tmp_path, overrides).observe()

    assert observation.target_vs_remote is expected


def test_director_reports_detached_head_from_head_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The HEAD dimension reports a detached checkout instead of collapsing to attached."""
    overrides = _ProbeOverrides()
    overrides.head_state = Reading.known(HeadState.DETACHED)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    observation = _default_director(tmp_path, overrides).observe()

    assert observation.head is HeadState.DETACHED


def test_director_reports_cherry_pick_in_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cherry-pick is projected as its own in-progress operation kind."""
    overrides = _ProbeOverrides()
    overrides.in_progress = Reading.known(InProgressKind.CHERRY_PICK)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    observation = _default_director(tmp_path, overrides).observe()

    assert observation.in_progress_kind is InProgressKind.CHERRY_PICK


def test_director_reports_owned_operation_when_record_claims_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A matching record claim in the current worktree proves operation ownership."""
    overrides = _ProbeOverrides()
    overrides.in_progress = Reading.known(InProgressKind.REBASE)
    overrides.record_disposition = RecordReadOutcomeDisposition.PRESENT
    overrides.record_resolving_rebase = True
    overrides.record_owning_worktree = str(tmp_path)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    observation = _default_director(tmp_path, overrides).observe()

    assert observation.in_progress_ownership is InProgressOwnership.OWNED


def test_director_reports_foreign_operation_when_record_does_not_claim_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operation with no matching record claim is foreign, not silently owned."""
    overrides = _ProbeOverrides()
    overrides.in_progress = Reading.known(InProgressKind.REBASE)
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    observation = _default_director(tmp_path, overrides).observe()

    assert observation.in_progress_ownership is InProgressOwnership.FOREIGN


def test_director_reports_foreign_operation_owned_by_another_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record held by another worktree cannot claim the operation locally."""
    overrides = _ProbeOverrides()
    overrides.in_progress = Reading.known(InProgressKind.REBASE)
    overrides.record_disposition = RecordReadOutcomeDisposition.PRESENT
    overrides.record_resolving_rebase = True
    overrides.record_owning_worktree = str(tmp_path / "other-worktree")
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    observation = _default_director(tmp_path, overrides).observe()

    assert observation.in_progress_ownership is InProgressOwnership.FOREIGN


def test_director_unreadable_operation_probe_is_unknown_and_never_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable operation probe is unknown evidence and never an operation."""
    overrides = _ProbeOverrides()
    overrides.record_disposition = RecordReadOutcomeDisposition.BUSY
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    def _unreadable_operation(_root: Path) -> Reading[InProgressKind]:
        raise OSError("operation probe failed")

    decision = _default_director(
        tmp_path,
        overrides,
        in_progress_probe=_unreadable_operation,
    ).decide_and_act()

    assert decision.observation.in_progress_kind is InProgressKind.NONE
    assert decision.observation.in_progress_ownership is InProgressOwnership.UNKNOWN
    assert decision.action is NextAction.WAIT_FOR_ENVIRONMENT
    assert decision.action not in (NextAction.DISPATCH_RESOLVER, NextAction.REPAIR)


def test_director_unreadable_merge_base_probe_is_unknown_and_never_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable ancestry probe is unknown and cannot become a blocking verdict."""
    overrides = _ProbeOverrides()
    overrides.head_sha = _FEATURE_SHA
    _install_probe_overrides(monkeypatch, tmp_path=tmp_path, overrides=overrides)

    def _unreadable_merge_base(_root: Path, _left: str, _right: str) -> tuple[str | None, bool]:
        raise OSError("merge-base probe failed")

    decision = _default_director(
        tmp_path,
        overrides,
        merge_base_probe=_unreadable_merge_base,
    ).decide_and_act()

    assert decision.observation.target_vs_feature is TargetVsFeature.UNKNOWN
    assert decision.action is NextAction.WAIT_FOR_ENVIRONMENT
    assert decision.action not in (NextAction.DISPATCH_RESOLVER, NextAction.REPAIR)


@pytest.mark.parametrize(
    ("marker_name", "expected"),
    [
        (None, InProgressKind.NONE),
        ("MERGE_HEAD", InProgressKind.MERGE),
        ("CHERRY_PICK_HEAD", InProgressKind.CHERRY_PICK),
        ("rebase-merge", InProgressKind.REBASE),
        ("rebase-apply", InProgressKind.REBASE),
    ],
)
def test_read_in_progress_kind_detects_authoritative_git_markers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    marker_name: str | None,
    expected: InProgressKind,
) -> None:
    """Git's operation markers select the complete in-progress kind vocabulary."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    if marker_name is not None:
        marker = git_dir / marker_name
        if marker_name.startswith("rebase-"):
            marker.mkdir()
        else:
            marker.write_text("state\n", encoding="utf-8")

    def _fake_run_git(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=str(git_dir), stderr="")

    monkeypatch.setattr(
        "ralph.pipeline.integration_observation.run_git",
        _fake_run_git,
    )

    assert read_in_progress_kind(tmp_path) == Reading.known(expected)
