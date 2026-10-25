"""Typed planning-sync seam tests.

:func:`ralph.pipeline._integration_owner_seam.decide_planning_sync` is
the planning gate's single owner seam: it charges the durable ledger
through the owner (a repeated hold escalates instead of looping), asks
the ONE worktree classifier for the tree verdict, and returns a typed
:class:`PlanningSyncVerdict` whose ``may_plan`` is DERIVED from the same
observation every gate sees — never a prose reason string parsed by the
caller.

The director is replaced by a recording stand-in and the porcelain
probe by a canned reading; the durable ledger is written by the
production code under ``tmp_path``. No real Git, no subprocess, no
sleeps. This is the ``audit_test_policy`` contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline import integration_director, integration_owner_seam
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_planning_sync
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import AttemptState
from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)

pytestmark = pytest.mark.timeout_seconds(10)

_TARGET_CONTAINED = (TargetVsFeature.EQUAL, TargetVsFeature.AHEAD)


def _observation(
    *,
    target_vs_feature: TargetVsFeature = TargetVsFeature.EQUAL,
    worktree: WorktreeState = WorktreeState.CLEAN,
) -> Observation:
    """Build the observation the recording director will serve."""
    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=worktree,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )


class _RecordingDirector:
    """Director stand-in serving one canned observation."""

    def __init__(
        self,
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
        observation: Observation,
    ) -> None:
        self.root = root
        self.target = target
        self.remote = remote
        self.agent_active = agent_active
        self.observation = observation

    def observe(self) -> Observation:
        """Return the canned observation the test installed."""
        return self.observation


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    observation: Observation,
    porcelain: str,
) -> None:
    """Install the director stand-in and the canned porcelain probe."""
    del tmp_path  # the stand-in does not read the filesystem

    def _factory(
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> _RecordingDirector:
        return _RecordingDirector(
            root,
            target=target,
            remote=remote,
            agent_active=agent_active,
            observation=observation,
        )

    monkeypatch.setattr(integration_director, "IntegrationDirector", _factory)
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.known(porcelain),
    )


def _ledger_path(tmp_path: Path) -> Path:
    return tmp_path / ".agent" / "integration_attempt_ledger.json"


# ---------------------------------------------------------------------------
# may_plan derivation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_vs_feature", _TARGET_CONTAINED)
def test_in_sync_clean_tree_allows_planning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_vs_feature: TargetVsFeature,
) -> None:
    """The feature contains the target tip and the gate allows dispatch."""
    _install(monkeypatch, tmp_path, _observation(target_vs_feature=target_vs_feature), "")

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.in_sync is True
    assert verdict.may_plan is True
    assert verdict.worktree.state is WorktreeState.CLEAN
    assert verdict.dirt_shape is DirtShape.NONE
    assert verdict.reason


@pytest.mark.parametrize("target_vs_feature", _TARGET_CONTAINED)
def test_tracked_dirt_holds_planning_with_a_typed_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_vs_feature: TargetVsFeature,
) -> None:
    """Blocking dirt holds planning; the verdict names the shape, not prose."""
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=target_vs_feature, worktree=WorktreeState.TRACKED_CHANGES),
        "M  src/app.py\n",
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.in_sync is True
    assert verdict.may_plan is False
    assert verdict.dirt_shape is DirtShape.STAGED_ONLY
    assert verdict.worktree.dispatch_blocked is True


def test_out_of_sync_branch_holds_planning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A branch behind the target holds planning and says integration runs."""
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=TargetVsFeature.BEHIND),
        "",
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.in_sync is False
    assert verdict.may_plan is False
    assert "target" in verdict.reason


def test_untracked_only_does_not_hold_planning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Scratch untracked files never hold planning (AC-10 untracked case)."""
    _install(monkeypatch, tmp_path, _observation(), "?? notes.txt\n")

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.may_plan is True
    assert verdict.dirt_shape is DirtShape.UNTRACKED_ONLY


def test_unknown_probe_holds_planning_without_a_verdict_guess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unreadable probe holds planning: back off, never guess."""
    observation = _observation(worktree=WorktreeState.UNKNOWN)
    _install(monkeypatch, tmp_path, observation, " M src/app.py\n")
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.unknown("git status timed out"),
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert verdict.may_plan is False
    assert verdict.worktree.dispatch is not verdict.worktree.dispatch_blocked
    assert verdict.worktree.needs_reobservation is True


# ---------------------------------------------------------------------------
# One owner, one charge.
# ---------------------------------------------------------------------------


def test_every_call_charges_the_durable_ledger_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A repeated hold spends the durable budget instead of looping."""
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=TargetVsFeature.BEHIND),
        " M src/app.py\n",
    )

    first = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))
    second = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert first.may_plan is False
    assert second.may_plan is False
    ledger = AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path)).read()
    signature = AttemptState().signature(first.observation)
    total = sum(
        count
        for (key_signature, _action), count in ledger.repeat_counts.items()
        if key_signature == signature
    )
    assert total == 2, f"a repeated hold must charge twice, charged {total}"


def test_the_verdict_carries_the_owner_action(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The seam action is the owner's ladder answer the boundary must act on."""
    _install(
        monkeypatch,
        tmp_path,
        _observation(target_vs_feature=TargetVsFeature.BEHIND),
        " M src/app.py\n",
    )

    verdict = decide_planning_sync(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert isinstance(verdict.action, IntegrationAction)
    assert verdict.action is IntegrationAction.REINTEGRATE_AGAINST_TARGET
    assert verdict.observation.target_vs_feature is TargetVsFeature.BEHIND


def test_configured_target_reaches_the_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The seam carries the configured target into the observation layer."""
    created: list[_RecordingDirector] = []

    def _factory(
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> _RecordingDirector:
        del agent_active
        director = _RecordingDirector(
            root,
            target=target,
            remote=remote,
            observation=_observation(),
        )
        created.append(director)
        return director

    monkeypatch.setattr(integration_director, "IntegrationDirector", _factory)
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.known(""),
    )

    decide_planning_sync(
        tmp_path,
        target="release",
        remote="tacos",
        ledger_path=_ledger_path(tmp_path),
    )

    assert created[0].target == "release"
    assert created[0].remote == "tacos"
