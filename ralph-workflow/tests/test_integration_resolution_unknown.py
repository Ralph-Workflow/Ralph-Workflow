"""Three-valued contract for every git probe on the integration dispatch path.

A probe that timed out, was killed, lost a lock, or stalled on I/O answers
UNKNOWN.  It must never mint a blocking reason, dispatch the conflict
resolver, or leave "unresolved" persisted behind it -- and a genuinely
conflicted tree must still be blocked, so the guarantee is a typed
distinction rather than a blanket loosening.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.git.merge import (
    MERGE_STATE_NONE,
    MERGE_STATE_UNKNOWN,
    UNMERGED_PATH_QUERY_FAILED,
    observe_unmerged_paths,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.integration_resolution import (
    RESOLVED,
    IntegrationBlockKind,
    IntegrationState,
    UnknownKind,
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
    persisted_integration_resolution_verdict,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.integration_resolution import _AssessedIntegrationResolutionVerdict


class _ProbeError(Exception):
    """Stand-in for a killed, timed-out, or lock-blocked git probe."""


def _raises(exc: Exception) -> Callable[[Path], object]:
    def _raise(_root: Path) -> object:
        raise exc

    return _raise


def _timeout(_args: object, **_kwargs: object) -> GitRunResult:
    raise subprocess.TimeoutExpired(cmd="git", timeout=1)


def _nonzero(_args: object, **_kwargs: object) -> GitRunResult:
    return GitRunResult((), 128, "", "fatal: unable to read index")


def _assert_unknown(verdict: _AssessedIntegrationResolutionVerdict) -> None:
    """No blocking reason, no resolver, nothing persisted, dispatch still open."""
    assert verdict.integration_state is IntegrationState.UNKNOWN
    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed is True
    assert verdict.reasons == ()
    assert verdict.recovery_executor is None
    assert verdict.blocking_reasons == ()
    assert verdict.block_kinds == ()
    assert verdict.unknown_kinds != ()


def test_timing_out_porcelain_probe_is_unknown_not_blocking(tmp_path: Path) -> None:
    """A probe that raised is UNKNOWN: dispatchable, unblocked, resolver-free."""
    state = RebaseState()
    verdict = inspect_integration_resolution(
        tmp_path,
        state,
        porcelain=_raises(subprocess.TimeoutExpired(cmd="git", timeout=1)),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    _assert_unknown(verdict)
    assert verdict.unknown_kinds == (UnknownKind.PORCELAIN_UNREADABLE,)
    assert_non_resolution_dispatch_allowed("development", verdict)
    assert state == RebaseState()
    assert persisted_integration_resolution_verdict(state) is None


def test_unreadable_porcelain_exit_is_unknown(tmp_path: Path) -> None:
    """A non-zero git exit is the same UNKNOWN, not a proven conflict."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    _assert_unknown(verdict)
    assert verdict.integration_ready is False


def test_timing_out_merge_state_read_is_unknown_not_blocking(tmp_path: Path) -> None:
    """A raised merge-state probe must not become "merge is in progress"."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=_raises(OSError("index.lock contention")),
    )

    _assert_unknown(verdict)
    assert verdict.unknown_kinds == (UnknownKind.MERGE_STATE_UNREADABLE,)


def test_merge_state_unknown_verdict_is_unknown_not_blocking(tmp_path: Path) -> None:
    """``MERGE_STATE_UNKNOWN`` used to be reported as an unreadable merge."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_UNKNOWN,
    )

    _assert_unknown(verdict)
    assert verdict.unknown_kinds == (UnknownKind.MERGE_STATE_UNREADABLE,)


def test_timing_out_rebase_state_read_is_unknown_not_blocking(tmp_path: Path) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=_raises(_ProbeError("rebase-merge/gitdir busy")),
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    _assert_unknown(verdict)
    assert verdict.unknown_kinds == (UnknownKind.REBASE_STATE_UNREADABLE,)


def test_proven_block_outranks_a_failed_probe(tmp_path: Path) -> None:
    """An unreadable probe beside a proven conflict still blocks."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, "UU src/a.py\n"),
        rebase_active=lambda _: (_ for _ in ()).throw(OSError("busy")),
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.KNOWN_BLOCKED
    assert verdict.dispatch_allowed is False
    assert verdict.block_kinds == (IntegrationBlockKind.UNMERGED_PATHS,)
    assert verdict.unknown_kinds == (UnknownKind.REBASE_STATE_UNREADABLE,)


@pytest.mark.parametrize("porcelain_code", ("UU", "AA", "DD", "AU", "UD", "DU", "UA"))
def test_genuinely_conflicted_tree_is_still_blocked(tmp_path: Path, porcelain_code: str) -> None:
    """Guard against over-correction: a real conflict keeps its typed kind."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, f"{porcelain_code} src/a.py\n"),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.KNOWN_BLOCKED
    assert verdict.dispatch_allowed is False
    assert verdict.block_kinds == (IntegrationBlockKind.UNMERGED_PATHS,)
    assert "unmerged paths remain" in verdict.reasons[0]
    assert verdict.recovery_executor == "rebase_conflict_resolution"
    with pytest.raises(RuntimeError, match="unmerged paths remain"):
        assert_non_resolution_dispatch_allowed("planning", verdict)


@pytest.mark.parametrize(
    ("merge_status", "kind"),
    (
        ("in_progress", IntegrationBlockKind.MERGE_IN_PROGRESS),
        ("unknown_later", IntegrationBlockKind.MERGE_IN_PROGRESS),
    ),
)
def test_in_progress_merge_state_still_blocks(
    tmp_path: Path, merge_status: str, kind: IntegrationBlockKind
) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: merge_status,
    )

    assert verdict.integration_state is IntegrationState.KNOWN_BLOCKED
    assert verdict.block_kinds == (kind,)


def test_active_rebase_still_blocks_with_its_own_kind(tmp_path: Path) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: True,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.KNOWN_BLOCKED
    assert verdict.block_kinds == (IntegrationBlockKind.REBASE_IN_PROGRESS,)


def test_timing_out_unmerged_paths_query_is_unknown_not_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken repository is UNKNOWN, never reported as a conflicted path."""
    monkeypatch.setattr("ralph.git.merge.run_git", _timeout)

    observed = observe_unmerged_paths(tmp_path)

    assert observed.query_ok is False
    assert observed.paths == ()
    assert UNMERGED_PATH_QUERY_FAILED not in observed.paths


def test_failed_unmerged_paths_query_never_reaches_the_dispatch_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure marker stays out of the blocking reasons it used to enter."""
    monkeypatch.setattr("ralph.git.merge.run_git", _nonzero)

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    _assert_unknown(verdict)
    assert UNMERGED_PATH_QUERY_FAILED not in " ".join(verdict.reasons)


def test_landed_durable_receipt_does_not_outrank_live_git_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt whose landing the target already carries is stale evidence.

    The receipt names an integrated feature SHA and the integration target
    already points at exactly that SHA, so the only work it describes has
    demonstrably happened. Live Git facts decide, and the dispatch gate
    opens even though the receipt is still on disk.
    """
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="c" * 40,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_record.read_record_outcome",
        lambda *_args, **_kwargs: RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.PRESENT, record=record
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.integration_resolution.observe_branch_sha",
        lambda _root, _name: ("c" * 40, True),
    )

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.KNOWN_GOOD
    assert verdict.dispatch_allowed is True
    assert verdict.reasons == ()
    assert verdict.recovery_executor is None


@pytest.mark.parametrize(
    "record",
    (
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha="b" * 40,
        ),
        IntegrationRecord(
            phase="integrated",
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha="b" * 40,
            integrated_feature_sha="c" * 40,
        ),
        IntegrationRecord(
            phase="integrated",
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha="b" * 40,
            integrated_feature_sha=None,
        ),
        IntegrationRecord(
            phase="integrated",
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha="b" * 40,
            integrated_feature_sha="c" * 40,
            owning_worktree="/somewhere/else",
        ),
    ),
)
def test_unproven_receipts_still_own_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: IntegrationRecord
) -> None:
    """Only a positive proof retires a receipt; everything else keeps owning."""
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_record.read_record_outcome",
        lambda *_args, **_kwargs: RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.PRESENT, record=record
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.integration_resolution.observe_branch_sha",
        lambda _root, _name: ("d" * 40, True),
    )

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.KNOWN_BLOCKED
    assert verdict.dispatch_allowed is False
    assert "durable integration record retained" in verdict.reasons[0]


def test_durable_resolution_exhaustion_outranks_an_unreadable_tree(tmp_path: Path) -> None:
    """An exhausted chain is a recorded fact, not an environmental failure."""
    state = RebaseState(
        resolution_exhausted=True, resolution_exhaustion_reason="all candidates failed"
    )
    verdict = inspect_integration_resolution(
        tmp_path,
        state,
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.KNOWN_BLOCKED
    assert verdict.dispatch_allowed is False
    assert verdict.reasons == ("all candidates failed",)
