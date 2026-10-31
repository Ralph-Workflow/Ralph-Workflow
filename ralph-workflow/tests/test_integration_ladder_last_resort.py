"""Rung 6 (last resort) executor tests: the hard reset behind six preconditions.

Pure fake-driven tests over the shared doubles in
:mod:`tests._integration_ladder_fakes` and the real
:class:`~ralph.pipeline.integration_attempt_ledger.AttemptLedger` on the
per-test ``tmp_path``: the reset runs ONLY when every precondition in
:data:`LAST_RESORT_PRECONDITIONS` holds, preservation runs immediately
before it, and a healed re-observation cancels it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_ladder import (
    LAST_RESORT_DISABLED_NO_SIGNATURE,
    LAST_RESORT_PRECONDITIONS,
    evaluate_last_resort_preconditions,
    last_resort_recovery,
)
from ralph.pipeline.integration_observation import InProgressKind
from tests._fake_git_clock import FakeClock
from tests._integration_ladder_fakes import (
    _SIG,
    _evidence,
    _FakePlumbing,
    _FakeQuarantineFs,
    _ledger,
    _seed_budget,
    _seed_salvage,
    _seed_terminal_rungs,
)

pytestmark = pytest.mark.timeout_seconds(30)


# ---------------------------------------------------------------------------
# Rung 6 — last resort.
# ---------------------------------------------------------------------------


def _seed_all_preconditions(ledger: AttemptLedger) -> None:
    _seed_terminal_rungs(ledger)
    _seed_salvage(ledger)
    _seed_budget(ledger)


def test_rung6_never_resets_when_any_precondition_is_unmet(tmp_path: Path) -> None:
    """An empty ledger means no rung was ever tried: the reset MUST NOT run."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )

    assert result.outcome == "failed"
    assert result.reason.startswith("failed:precondition-unmet:")
    assert not any(call.startswith("reset_hard") for call in git.calls)
    # Every precondition's evidence is recorded all the same; the
    # re-observation one IS met here (the signatures match) — the reset
    # is still refused by the five unmet rung/salvage/budget ones.
    evidence = _evidence(tmp_path)
    for name in LAST_RESORT_PRECONDITIONS:
        if name == "fresh_reobservation_confirms_blocking_state":
            assert evidence[f"precondition:{name}"].startswith("met")
        else:
            assert evidence[f"precondition:{name}"].startswith("unmet")


def test_rung6_healed_state_cancels_the_reset(tmp_path: Path) -> None:
    """A fresh re-observation that no longer matches cancels the reset."""
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing()

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature="clean|clean|none|none|attached",
    )

    assert result.outcome == "failed"
    assert result.reason == "failed:precondition-unmet:fresh_reobservation_confirms_blocking_state"
    assert not any(call.startswith("reset_hard") for call in git.calls)


def test_rung6_resets_only_after_all_six_preconditions_hold(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha"}))

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
        untracked_paths=("scratch.txt",),
        in_progress_kind=InProgressKind.REBASE,
    )

    assert result.outcome == "succeeded"
    assert "abort_operation:rebase" in git.calls
    assert "reset_hard:tipsha" in git.calls
    assert "clean_untracked" in git.calls
    assert "remove_stale_locks" in git.calls
    # Preserved copies are never pruned by the reset.
    assert "prune_landed_backup_refs" not in git.calls
    evidence = _evidence(tmp_path)
    for name in LAST_RESORT_PRECONDITIONS:
        assert evidence[f"precondition:{name}"].startswith("met"), name


def test_rung6_runs_preservation_immediately_before_the_reset(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha", "refs/ralph-backup/fake"}))

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
        paths=("src/a.py",),
    )

    assert result.outcome == "succeeded"
    order = [call for call in git.calls if call in ("commit_dirt", "reset_hard:tipsha")]
    assert order == ["commit_dirt", "reset_hard:tipsha"]
    assert _evidence(tmp_path)["preserved:alternate_preservation"] == "refs/ralph-backup/fake"


def test_rung6_resets_to_target_when_the_feature_tip_is_unusable(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset())

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )

    assert result.outcome == "succeeded"
    assert "reset_hard:main" in git.calls


def test_rung6_recreates_the_worktree_when_even_the_reset_fails(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha"}), fail_first_reset=True)

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )

    assert result.outcome == "succeeded"
    assert "recreate_worktree:feature" in git.calls
    assert git.calls.count("reset_hard:tipsha") == 2


def test_rung6_abort_failure_deletes_operation_state(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha"}), fail_abort=True)

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
        in_progress_kind=InProgressKind.MERGE,
    )

    assert result.outcome == "succeeded"
    assert "remove_operation_state:merge" in git.calls


def test_evaluate_preconditions_reports_every_one() -> None:
    state = AttemptLedgerState(ladder_evidence={"rung:save_and_restore": "succeeded:x"})
    results = evaluate_last_resort_preconditions(
        state, decided_signature=_SIG, fresh_signature=_SIG
    )
    assert tuple(results) == LAST_RESORT_PRECONDITIONS
    assert results["rungs_1_to_5_tried_or_shown_impossible"].startswith("unmet")
    assert results["ladder_attempt_budget_spent"].startswith("unmet")
    assert results["fresh_reobservation_confirms_blocking_state"].startswith("met")


def test_rung6_without_a_blocking_signature_is_explicitly_disabled(tmp_path: Path) -> None:
    """A caller that supplies no reconfirmed blocking signature cannot run the reset.

    Even with every ledger precondition seeded, the rung refuses with a
    named ``disabled`` verdict rather than a misleading precondition miss,
    and never touches the tree.
    """
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha"}))

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature="",
        fresh_signature="",
    )

    assert result.outcome == "failed"
    assert result.reason == LAST_RESORT_DISABLED_NO_SIGNATURE
    assert git.calls == []
    assert LAST_RESORT_DISABLED_NO_SIGNATURE in _evidence(tmp_path).values()
