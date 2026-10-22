"""AC9: every gate gives the same answer to "is the tree clean?".

Cleanliness used to be decided independently by at least five probes
(``git/operations.is_repo_clean``, the boundary shim, the catch-up
copy, the rebase preconditions, and the director's own porcelain
projection). Each defined "clean" slightly differently, so one seam
integrated while another refused — the "three gates, three answers"
symptom in ``PRODUCT_CRITERIA.md``.

This suite pins the one contract, once, for every
:class:`~ralph.pipeline.integration_observation.WorktreeState` plus the
unknown case, and then pins the PARITY claim: for the same underlying
tree state, every probe reachable from
:func:`~ralph.pipeline.integration_observation.classify_worktree`
returns the identical verdict. A second owner inside the migrated
modules would show up here as a disagreement.

Everything runs on in-memory fakes and ``tmp_path``. No real git, no
``time.sleep``, no real clock — the ``audit_test_policy`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.pipeline import auto_integrate_worktree_state as legacy
from ralph.pipeline import integration_observation as obs
from ralph.pipeline.integration_observation import (
    HeadState,
    Reading,
    WorktreeDispatch,
    WorktreeState,
    WorktreeVerdict,
    classify_worktree,
    read_head_state,
    read_worktree,
    read_worktree_porcelain,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(5)


# ---------------------------------------------------------------------------
# The written contract, one row per enum member.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ContractRow:
    """One observed tree state, the reading that produces it, and the contract.

    ``porcelain`` is the line-oriented v1 text; ``blob`` is the same
    records as ``git status --porcelain -z`` emits them, so every known
    row is also a probe-shape parity case: the line-oriented read and
    the ``-z`` read of the SAME tree must classify identically.
    """

    state: WorktreeState
    porcelain: str
    is_clean: bool
    dispatch: WorktreeDispatch

    @property
    def reading(self) -> Reading[str]:
        """The probe reading a successful porcelain call would produce."""
        return Reading.known(self.porcelain)

    @property
    def blob(self) -> str:
        """The same records as ``git status --porcelain -z`` emits them."""
        return "".join(f"{line}\0" for line in self.porcelain.splitlines())


#: Every KNOWN ``WorktreeState`` member exactly once. The exhaustive
#: coverage test below fails if the enum grows a member this table misses.
_CONTRACT: tuple[_ContractRow, ...] = (
    _ContractRow(WorktreeState.CLEAN, "", True, WorktreeDispatch.ALLOWED),
    _ContractRow(
        WorktreeState.TRACKED_CHANGES,
        " M src/app.py\n",
        False,
        WorktreeDispatch.BLOCKED,
    ),
    _ContractRow(
        WorktreeState.STAGED,
        "M  src/app.py\n",
        False,
        WorktreeDispatch.BLOCKED,
    ),
    _ContractRow(
        WorktreeState.UNMERGED,
        "UU src/app.py\n",
        False,
        WorktreeDispatch.BLOCKED,
    ),
    _ContractRow(
        WorktreeState.UNTRACKED,
        "?? scratch-notes.txt\n",
        True,
        WorktreeDispatch.ALLOWED,
    ),
)

#: The unknown row has no porcelain text by construction: a blob that
#: could be read never yields it. It models the environment failures the
#: commit loop produced — a timeout, a kill, lock contention, an I/O stall.
_UNKNOWN_REASON = "git status --porcelain timed out after 120s"
_UNKNOWN: _ContractRow = _ContractRow(
    WorktreeState.UNKNOWN,
    "",
    False,
    WorktreeDispatch.UNKNOWN,
)

#: Every enum member, the unknown case included.
_ALL_ROWS: tuple[_ContractRow, ...] = (*_CONTRACT, _UNKNOWN)


def _contract_row(state: WorktreeState) -> _ContractRow:
    """The contract row for ``state``; fails loudly if the table has a gap."""
    for row in _ALL_ROWS:
        if row.state is state:
            return row
    raise AssertionError(f"no contract row for {state}")


def _decision(verdict: WorktreeVerdict) -> tuple[WorktreeState, bool, WorktreeDispatch]:
    """The part of a verdict that two callers must never disagree about.

    ``reason`` is deliberately excluded: it is the text of the condition
    observed in THIS attempt, so it differs between "a probe reported
    this" and "a caller handed me a bare state". Collapsing them would
    mean inventing a reason nobody observed.
    """
    return verdict.state, verdict.is_clean, verdict.dispatch


# ---------------------------------------------------------------------------
# Fakes — a scripted ``run_git`` that never spawns a process.
# ---------------------------------------------------------------------------


@dataclass
class _FakeGit:
    """Scripted ``run_git`` returning one fixed answer and recording argv.

    ``raise_error`` models an environment failure git could not even
    report on: a killed child, a timed-out call, a lock stall. ``argv``
    accumulates as an immutable tuple so the recorder cannot grow without
    bound in a test that loops.
    """

    returncode: int = 0
    stdout: str = ""
    raise_error: bool = False
    argv: tuple[tuple[str, ...], ...] = ()

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        label: str,
        options: object | None = None,
    ) -> GitRunResult:
        """Record the invocation and answer from the script."""
        assert cwd is not None  # every probe passes a root
        assert label  # every probe is labelled for the process manager
        assert options is None  # the observation probes never pass options
        self.argv = (*self.argv, tuple(args))
        if self.raise_error:
            raise TimeoutError(_UNKNOWN_REASON)
        return GitRunResult(
            args=tuple(args),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr="" if self.returncode == 0 else "fatal: could not read index",
        )


def _install(fake: _FakeGit, monkeypatch: pytest.MonkeyPatch) -> _FakeGit:
    """Point ``run_git`` at ``fake`` in both owned probe modules."""
    for target in (obs, legacy):
        monkeypatch.setattr(target, "run_git", fake.run)
    return fake


# ---------------------------------------------------------------------------
# The contract itself.
# ---------------------------------------------------------------------------


def test_contract_table_covers_every_worktree_state_member() -> None:
    """Every enum member has exactly one contract row — no gap, no duplicate."""
    assert {row.state for row in _ALL_ROWS} == set(WorktreeState)
    assert len(_ALL_ROWS) == len(WorktreeState)
    for state in WorktreeState:
        assert _contract_row(state).state is state


@pytest.mark.parametrize(("row"), _ALL_ROWS, ids=lambda row: row.state.value)
def test_classifier_honours_the_contract_for_every_state(row: _ContractRow) -> None:
    """The contract holds for every state, read as a probe and read as a state."""
    reading = (
        Reading.unknown(_UNKNOWN_REASON) if row.state is WorktreeState.UNKNOWN else row.reading
    )

    from_probe = classify_worktree(reading)
    from_state = classify_worktree(row.state)

    assert from_probe.state is row.state
    assert from_probe.is_clean is row.is_clean
    assert from_probe.dispatch is row.dispatch
    assert from_probe.reason
    assert _decision(from_state) == _decision(from_probe)


def test_unknown_probe_is_neither_clean_nor_blocking() -> None:
    """An unreadable probe backs off; it is never clean and never blocking."""
    verdict = classify_worktree(Reading.unknown(_UNKNOWN_REASON))

    assert verdict.state is WorktreeState.UNKNOWN
    assert verdict.is_clean is False
    assert verdict.dispatch is WorktreeDispatch.UNKNOWN
    assert verdict.dispatch_allowed is False
    assert verdict.dispatch_blocked is False
    assert verdict.needs_reobservation is True
    assert _UNKNOWN_REASON in verdict.reason


def test_unknown_verdict_does_not_depend_on_the_failure_text() -> None:
    """Two different environment failures still produce one unknown verdict."""
    first = classify_worktree(Reading.unknown("timeout"))
    second = classify_worktree(Reading.unknown("index.lock exists"))

    assert first.state is second.state is WorktreeState.UNKNOWN
    assert first.dispatch is second.dispatch is WorktreeDispatch.UNKNOWN


def test_untracked_only_is_clean_for_dispatch_but_still_reported() -> None:
    """A scratch file never blocks a gate, yet the commit seam still sees it.

    Blocking on untracked paths turned a per-file, git-detectable hazard
    into a run-wide outage: one scratch file left by a phase disabled
    integration for every later seam.
    """
    verdict = classify_worktree(Reading.known("?? scratch-notes.txt\n?? tmp/out.bin\n"))

    assert verdict.state is WorktreeState.UNTRACKED
    assert verdict.is_clean is True
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


def test_unmerged_outranks_tracked_and_staged() -> None:
    """A conflicted index is UNMERGED no matter what else the tree holds."""
    verdict = classify_worktree(Reading.known("UU src/app.py\n M src/other.py\nM  staged.py\n"))

    assert verdict.state is WorktreeState.UNMERGED
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_every_unmerged_index_code_classifies_as_unmerged() -> None:
    """Each unmerged XY code is a conflict; the codes are not hard-coded twice."""
    for code in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"):
        verdict = classify_worktree(Reading.known(f"{code} src/app.py\n"))
        assert verdict.state is WorktreeState.UNMERGED, code


def test_tracked_change_outranks_untracked() -> None:
    """Mixed porcelain with real tracked work is blocking, not 'scratch only'."""
    verdict = classify_worktree(Reading.known(" M src/app.py\n?? scratch-notes.txt\n"))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_staged_and_modified_together_is_blocking() -> None:
    """``MM`` (staged AND modified) is dirty whichever single label it gets."""
    verdict = classify_worktree(Reading.known("MM src/app.py\n"))

    assert verdict.is_clean is False
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_ignored_entries_never_block() -> None:
    """An ignored build artefact is not work."""
    verdict = classify_worktree(Reading.known("!! build/output.bin\n"))

    assert verdict.state is WorktreeState.CLEAN
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


# ---------------------------------------------------------------------------
# Parity: one owner, no divergence.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_state_arm_agrees_with_the_porcelain_arm(row: _ContractRow) -> None:
    """A caller holding a projected state gets the same verdict as a probe.

    Both arms of :func:`classify_worktree` run through one contract
    table, so migrating a gate from "project the state, then decide" to
    "classify the reading" cannot change the answer.
    """
    assert classify_worktree(row.state) == classify_worktree(row.reading)


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_line_and_nul_porcelain_shapes_agree(row: _ContractRow) -> None:
    """The same tree read two ways classifies identically."""
    assert classify_worktree(Reading.known(row.blob)) == classify_worktree(row.reading)


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_probe_helpers_agree_with_the_classifier(
    row: _ContractRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read_worktree`` returns exactly what the classifier returns.

    This is the AC9 parity claim for the probe path: observing through
    the real probe wiring and classifying the same blob directly are
    indistinguishable.
    """
    _install(_FakeGit(stdout=row.porcelain), monkeypatch)

    observed = read_worktree(tmp_path)

    assert observed.is_known_good
    assert observed.value == classify_worktree(row.reading)


def test_legacy_boolean_shim_delegates_instead_of_re_deriving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_verdict`` is the classifier's verdict verbatim — not a copy of it.

    If the shim ever re-derives cleanliness from the blob itself, this
    equality is the first thing that breaks.
    """
    for row in _CONTRACT:
        _install(_FakeGit(stdout=row.porcelain), monkeypatch)

        assert legacy._verdict(tmp_path) == classify_worktree(row.reading)


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_legacy_boolean_shim_matches_the_verdict_for_every_known_state(
    row: _ContractRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy ``bool`` agrees with the verdict wherever a bool can agree."""
    _install(_FakeGit(stdout=row.porcelain), monkeypatch)

    assert legacy._worktree_is_clean(tmp_path) is (row.dispatch is WorktreeDispatch.ALLOWED)


def test_legacy_boolean_shim_pins_its_one_known_limitation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare ``bool`` cannot say "back off", so it still fails closed.

    A non-zero git exit is ``UNKNOWN`` at the classifier — never clean,
    never blocking — but the legacy signature promises a ``bool`` and
    ``tests/test_auto_integrate_boundary_cleanliness.py`` pins ``False``
    for an unreadable tree. The deviation is asserted here so it stays
    visible until every caller migrates to
    :func:`~ralph.pipeline.integration_observation.classify_worktree`,
    instead of being silently rediscovered as a gate disagreement.
    """
    fake = _install(_FakeGit(returncode=1), monkeypatch)

    verdict = legacy._verdict(tmp_path)

    assert verdict.state is WorktreeState.UNKNOWN
    assert verdict.dispatch is WorktreeDispatch.UNKNOWN
    assert verdict.dispatch_blocked is False
    assert legacy._worktree_is_clean(tmp_path) is False
    assert set(fake.argv) == {
        (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no")
    }


# ---------------------------------------------------------------------------
# The probes must not swallow a failure into a clean reading.
# ---------------------------------------------------------------------------


def test_porcelain_probe_observes_untracked_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observation probe asks git for untracked files.

    ``--untracked-files=no`` cannot tell "scratch files only" from "work";
    it answers "clean" about a tree that holds uncommitted work.
    """
    fake = _install(_FakeGit(stdout="?? scratch-notes.txt\0"), monkeypatch)

    reading = read_worktree_porcelain(tmp_path)

    assert reading.is_known_good
    assert reading.value == "?? scratch-notes.txt\0"
    (argv,) = fake.argv
    assert "--porcelain" in argv
    assert "-z" in argv
    assert "--untracked-files=normal" in argv
    assert "--untracked-files=no" not in argv


@pytest.mark.parametrize(
    "fake",
    (
        _FakeGit(returncode=1),
        _FakeGit(returncode=128),
        _FakeGit(raise_error=True),
    ),
    ids=("nonzero", "fatal", "raised"),
)
def test_failed_porcelain_probe_is_unknown_never_empty(
    fake: _FakeGit,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed porcelain probe is unknown — never the empty "clean" blob.

    Returning ``""`` on failure is how the commit loop's timed-out
    ``git status`` became "no uncommitted work", then "integration
    resolved", then a phase that moved on over an unresolved tree.
    """
    _install(fake, monkeypatch)

    reading = read_worktree_porcelain(tmp_path)

    assert reading.is_unknown
    assert reading.value is None
    assert reading.reason


def test_failed_porcelain_probe_reads_as_unknown_not_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: an unreadable tree is neither clean nor blocking."""
    _install(_FakeGit(raise_error=True), monkeypatch)

    verdict = read_worktree(tmp_path)

    assert verdict.is_unknown
    assert verdict.value is None


def test_read_worktree_surfaces_the_probe_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unknown verdict names the condition observed in THIS attempt."""
    _install(_FakeGit(returncode=128), monkeypatch)

    verdict = read_worktree(tmp_path)

    assert verdict.is_unknown
    assert "128" in (verdict.reason or "")


# ---------------------------------------------------------------------------
# HEAD: a genuinely detached HEAD must be reported as detached.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "expected"),
    (
        (0, HeadState.ATTACHED),
        (1, HeadState.DETACHED),
    ),
    ids=("symbolic-ref", "not-a-symbolic-ref"),
)
def test_head_probe_reports_attached_and_detached(
    returncode: int,
    expected: HeadState,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``git symbolic-ref -q HEAD`` exits 1 exactly when HEAD is detached."""
    _install(_FakeGit(returncode=returncode), monkeypatch)

    assert read_head_state(tmp_path).value is expected


@pytest.mark.parametrize(
    "fake",
    (_FakeGit(returncode=128), _FakeGit(raise_error=True)),
    ids=("fatal", "raised"),
)
def test_head_probe_is_unknown_when_git_cannot_answer(
    fake: _FakeGit,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable HEAD is ``UNKNOWN`` — never read as "attached"."""
    _install(fake, monkeypatch)

    reading = read_head_state(tmp_path)

    assert reading.is_unknown
    assert reading.value is None
    assert reading.reason


def test_observation_unknown_helper_covers_the_worktree_dimension() -> None:
    """An unknown worktree reading makes the observation unknown-capable."""
    observation = obs.Observation(
        target_vs_feature=obs.TargetVsFeature.BEHIND,
        target_vs_remote=obs.TargetVsRemote.EQUAL,
        worktree=classify_worktree(Reading.unknown("git status timed out")).state,
        head=HeadState.ATTACHED,
        in_progress_kind=obs.InProgressKind.NONE,
        in_progress_ownership=obs.InProgressOwnership.OWNED,
        record=obs.IntegrationRecordState.NONE,
        agent_active=False,
    )

    assert observation.has_unknown()


def test_worktree_verdict_helpers_are_mutually_exclusive() -> None:
    """``ALLOWED``, ``BLOCKED`` and ``UNKNOWN`` never both answer one verdict."""
    for row in _ALL_ROWS:
        reading = (
            Reading.unknown(_UNKNOWN_REASON) if row.state is WorktreeState.UNKNOWN else row.reading
        )
        verdict: WorktreeVerdict = classify_worktree(reading)
        flags = (
            verdict.dispatch_allowed,
            verdict.dispatch_blocked,
            verdict.needs_reobservation,
        )
        assert sum(flags) == 1, row.state
        assert not (verdict.dispatch_allowed and verdict.dispatch_blocked)
