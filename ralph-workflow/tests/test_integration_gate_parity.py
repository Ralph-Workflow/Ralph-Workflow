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


@dataclass(frozen=True)
class _ContractRow:
    state: WorktreeState
    porcelain: str
    is_clean: bool
    dispatch: WorktreeDispatch

    @property
    def reading(self) -> Reading[str]:
        return Reading.known(self.porcelain)

    @property
    def blob(self) -> str:
        return "".join(f"{line}\0" for line in self.porcelain.splitlines())


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

_UNKNOWN_REASON = "git status --porcelain timed out after 120s"
_UNKNOWN: _ContractRow = _ContractRow(
    WorktreeState.UNKNOWN,
    "",
    False,
    WorktreeDispatch.UNKNOWN,
)

_ALL_ROWS: tuple[_ContractRow, ...] = (*_CONTRACT, _UNKNOWN)


def _contract_row(state: WorktreeState) -> _ContractRow:
    for row in _ALL_ROWS:
        if row.state is state:
            return row
    raise AssertionError(f"no contract row for {state}")


def _decision(verdict: WorktreeVerdict) -> tuple[WorktreeState, bool, WorktreeDispatch]:
    return verdict.state, verdict.is_clean, verdict.dispatch


@dataclass
class _FakeGit:
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
        assert cwd is not None
        assert label
        assert options is None
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
    for target in (obs, legacy):
        monkeypatch.setattr(target, "run_git", fake.run)
    return fake


def test_contract_table_covers_every_worktree_state_member() -> None:
    assert {row.state for row in _ALL_ROWS} == set(WorktreeState)
    assert len(_ALL_ROWS) == len(WorktreeState)
    for state in WorktreeState:
        assert _contract_row(state).state is state


@pytest.mark.parametrize(("row"), _ALL_ROWS, ids=lambda row: row.state.value)
def test_classifier_honours_the_contract_for_every_state(row: _ContractRow) -> None:
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
    verdict = classify_worktree(Reading.unknown(_UNKNOWN_REASON))

    assert verdict.state is WorktreeState.UNKNOWN
    assert verdict.is_clean is False
    assert verdict.dispatch is WorktreeDispatch.UNKNOWN
    assert verdict.dispatch_allowed is False
    assert verdict.dispatch_blocked is False
    assert verdict.needs_reobservation is True
    assert _UNKNOWN_REASON in verdict.reason


def test_unknown_verdict_does_not_depend_on_the_failure_text() -> None:
    first = classify_worktree(Reading.unknown("timeout"))
    second = classify_worktree(Reading.unknown("index.lock exists"))

    assert first.state is second.state is WorktreeState.UNKNOWN
    assert first.dispatch is second.dispatch is WorktreeDispatch.UNKNOWN


def test_untracked_only_is_clean_for_dispatch_but_still_reported() -> None:
    verdict = classify_worktree(Reading.known("?? scratch-notes.txt\n?? tmp/out.bin\n"))

    assert verdict.state is WorktreeState.UNTRACKED
    assert verdict.is_clean is True
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


def test_unmerged_outranks_tracked_and_staged() -> None:
    verdict = classify_worktree(Reading.known("UU src/app.py\n M src/other.py\nM  staged.py\n"))

    assert verdict.state is WorktreeState.UNMERGED
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_every_unmerged_index_code_classifies_as_unmerged() -> None:
    for code in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"):
        verdict = classify_worktree(Reading.known(f"{code} src/app.py\n"))
        assert verdict.state is WorktreeState.UNMERGED, code


def test_tracked_change_outranks_untracked() -> None:
    verdict = classify_worktree(Reading.known(" M src/app.py\n?? scratch-notes.txt\n"))

    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_staged_and_modified_together_is_blocking() -> None:
    verdict = classify_worktree(Reading.known("MM src/app.py\n"))

    assert verdict.is_clean is False
    assert verdict.dispatch is WorktreeDispatch.BLOCKED


def test_ignored_entries_never_block() -> None:
    verdict = classify_worktree(Reading.known("!! build/output.bin\n"))

    assert verdict.state is WorktreeState.CLEAN
    assert verdict.dispatch is WorktreeDispatch.ALLOWED


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_state_arm_agrees_with_the_porcelain_arm(row: _ContractRow) -> None:
    assert classify_worktree(row.state) == classify_worktree(row.reading)


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_line_and_nul_porcelain_shapes_agree(row: _ContractRow) -> None:
    assert classify_worktree(Reading.known(row.blob)) == classify_worktree(row.reading)


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_probe_helpers_agree_with_the_classifier(
    row: _ContractRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(_FakeGit(stdout=row.porcelain), monkeypatch)

    observed = read_worktree(tmp_path)

    assert observed.is_known_good
    assert observed.value == classify_worktree(row.reading)


def test_legacy_boolean_shim_delegates_instead_of_re_deriving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for row in _CONTRACT:
        _install(_FakeGit(stdout=row.porcelain), monkeypatch)

        assert legacy._verdict(tmp_path) == classify_worktree(row.reading)


@pytest.mark.parametrize(("row"), _CONTRACT, ids=lambda row: row.state.value)
def test_legacy_boolean_shim_matches_the_verdict_for_every_known_state(
    row: _ContractRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(_FakeGit(stdout=row.porcelain), monkeypatch)

    assert legacy._worktree_is_clean(tmp_path) is (row.dispatch is WorktreeDispatch.ALLOWED)


def test_legacy_boolean_shim_pins_its_one_known_limitation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install(_FakeGit(returncode=1), monkeypatch)

    verdict = legacy._verdict(tmp_path)

    assert verdict.state is WorktreeState.UNKNOWN
    assert verdict.dispatch is WorktreeDispatch.UNKNOWN
    assert verdict.dispatch_blocked is False
    assert legacy._worktree_is_clean(tmp_path) is False
    assert set(fake.argv) == {
        (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no")
    }


def test_porcelain_probe_observes_untracked_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    _install(fake, monkeypatch)

    reading = read_worktree_porcelain(tmp_path)

    assert reading.is_unknown
    assert reading.value is None
    assert reading.reason


def test_failed_porcelain_probe_reads_as_unknown_not_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(_FakeGit(raise_error=True), monkeypatch)

    verdict = read_worktree(tmp_path)

    assert verdict.is_unknown
    assert verdict.value is None


def test_read_worktree_surfaces_the_probe_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(_FakeGit(returncode=128), monkeypatch)

    verdict = read_worktree(tmp_path)

    assert verdict.is_unknown
    assert "128" in (verdict.reason or "")


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
    _install(fake, monkeypatch)

    reading = read_head_state(tmp_path)

    assert reading.is_unknown
    assert reading.value is None
    assert reading.reason


def test_observation_unknown_helper_covers_the_worktree_dimension() -> None:
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
