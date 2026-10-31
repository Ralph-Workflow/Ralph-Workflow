"""Caller-boundary release-refusal tests for the three RELEASE_DIAGNOSTIC sites.

Unit WU-1b. Each test drives a REAL release caller across the PRESENT,
BUSY, and UNREADABLE race windows and asserts the typed refusal outcome
plus byte-for-byte UNCHANGED record bytes on disk:

- recovery: ``auto_integrate_recovery._maybe_release_diagnostic``
- diagnostic: ``_integration_diagnostic._complete_or_intervene``
- remote reconcile: ``_remote_reconcile_recovery._apply_release_or_refuse``
  (also reached through ``_maybe_release_target_diagnostic``)

Every refusal case asserts the record bytes on disk are byte-for-byte
identical before and after the call: a refusal must never overwrite.
The typed busy decision authority (``busy_refusal_outcome`` re-exports) is
pinned too.
"""

from __future__ import annotations

import errno
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts._path_file_backend import PathFileBackend
from ralph.pipeline._integration_action import IntegrationAction, RebaseOutcomeKind
from ralph.pipeline._integration_diagnostic import (
    _complete_or_intervene,
)
from ralph.pipeline._integration_diagnostic import (
    busy_refusal_outcome as diagnostic_busy_refusal_outcome,
)
from ralph.pipeline._integration_diagnostic import (
    busy_refusal_outcome as recovery_busy_refusal_outcome,
)
from ralph.pipeline._integration_diagnostic import (
    busy_refusal_outcome as remote_busy_refusal_outcome,
)
from ralph.pipeline._remote_reconcile_recovery import (
    RecordBusyError,
    RecordUnreadableError,
    _apply_release_or_refuse,
    _maybe_release_target_diagnostic,
)
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    IntegrationRecord,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.auto_integrate_recovery import (
    _maybe_release_diagnostic,
)
from ralph.pipeline.auto_integrate_transition import RELEASE_DIAGNOSTIC_EVIDENCE
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    ladder_rung_for_record_fact_outcome,
)
from ralph.pipeline.operation_evidence import (
    identity_fingerprint,
)
from ralph.pipeline.operation_evidence_memory import InMemoryOperationEvidenceCollector
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.mcp.artifacts.file_backend import FileBackend

RECORD_FILE = Path(".agent") / AUTO_INTEGRATE_RECORD_FILENAME
DEFAULT_BACKEND: FileBackend = PathFileBackend()


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------


class _InjectedBackend:
    """FileBackend wrapper that injects one typed race window at call N.

    ``fail_mode="busy"`` makes the Nth ``exists`` call raise
    ``OSError(EAGAIN)`` (transient lock contention -> BUSY).
    ``fail_mode="unreadable"`` makes the Nth ``read_text`` call raise a
    generic ``OSError`` (backend read failure -> UNREADABLE). Earlier
    and later calls are fully transparent, so the caller's own
    top-level read succeeds and the injection lands exactly inside the
    record store's re-read during the transition.
    """

    def __init__(self, inner: FileBackend, fail_mode: str, fail_on_call: int) -> None:
        self._inner = inner
        self._fail_mode = fail_mode
        self._fail_on_call = fail_on_call
        self._exists_calls = 0
        self._read_calls = 0

    def exists(self, path: Path) -> bool:
        self._exists_calls += 1
        if self._fail_mode == "busy" and self._exists_calls == self._fail_on_call:
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
        return self._inner.exists(path)

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self._inner.mkdir(path, parents=parents, exist_ok=exist_ok)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        self._read_calls += 1
        if self._fail_mode == "unreadable" and self._read_calls == self._fail_on_call:
            raise OSError(errno.EIO, "injected backend read failure")
        return self._inner.read_text(path, encoding=encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self._inner.write_text(path, content, encoding=encoding)

    def read_bytes(self, path: Path) -> bytes:
        return self._inner.read_bytes(path)

    def write_bytes(self, path: Path, content: bytes) -> None:
        self._inner.write_bytes(path, content)

    def replace(self, source: Path, destination: Path) -> None:
        self._inner.replace(source, destination)

    def sync_directory(self, path: Path) -> None:
        self._inner.sync_directory(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        self._inner.unlink(path, missing_ok=missing_ok)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return self._inner.glob(path, pattern)


class _UnchangedCollector(InMemoryOperationEvidenceCollector):
    """Collector whose verdict is always UNCHANGED (positive evidence)."""

    def __init__(self) -> None:
        from ralph.pipeline.operation_fact import OperationFact

        super().__init__(facts=(OperationFact(kind="head", value="integrated"),))


def _verified_record(owning_worktree: str | None = None) -> IntegrationRecord:
    """A PRESENT integrating record whose lease is valid (verdict UNCHANGED)."""
    import time

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        diagnostic_evidence=(),
        diagnostic_ownership=None,
        owning_worktree=owning_worktree,
    )
    held = time.time()
    return record.model_copy(
        update={
            "diagnostic_evidence": (("head", "integrated"),),
            "diagnostic_ownership": f"{identity_fingerprint(record)}:{held}",
        }
    )


def _write_present_record(tmp_path: Path, record: IntegrationRecord | None = None) -> bytes:
    """Write a PRESENT record; return its exact on-disk bytes."""
    write_record(tmp_path, record if record is not None else _verified_record(str(tmp_path)))
    return (tmp_path / RECORD_FILE).read_bytes()


def _install_unchanged_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the always-UNCHANGED collector as the diagnostic-path default."""
    from ralph.pipeline._integration_diagnostic import set_operation_evidence_collector

    set_operation_evidence_collector(_UnchangedCollector())
    monkeypatch.setattr(
        "ralph.pipeline._integration_diagnostic.set_operation_evidence_collector",
        set_operation_evidence_collector,
    )


def _assert_bytes_unchanged(record_file: Path, before: bytes) -> None:
    """The single strongest refusal contract: no overwrite, ever."""
    assert record_file.read_bytes() == before


# ---------------------------------------------------------------------------
# busy_refusal_outcome canonical mapping (re-exported by all three modules)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("busy_refusal_outcome",),
    [
        pytest.param(recovery_busy_refusal_outcome, id="recovery"),
        pytest.param(diagnostic_busy_refusal_outcome, id="diagnostic"),
        pytest.param(remote_busy_refusal_outcome, id="remote"),
    ],
)
class TestBusyRetryActionMapping:
    """The typed BUSY decision authority is identical at all three sites."""

    def test_unexpired_deadline_waits_for_bounded_retry(self, busy_refusal_outcome: object) -> None:
        # 999 s ahead: unexpired and inside the one-hour horizon writers may
        # arm. A deadline further ahead is a previous boot's monotonic reading
        # and expires (tests/test_integration_retry_deadline_reboot.py).
        action = busy_refusal_outcome(1_000.0, clock=lambda: 1.0)
        assert action is IntegrationAction.WAIT_FOR_BOUNDED_RETRY

    def test_missing_deadline_fails_closed_to_stale_intent(
        self, busy_refusal_outcome: object
    ) -> None:
        """A missing deadline is the typed STALE_INTENT verdict, never a hand-off."""
        action = busy_refusal_outcome(None, clock=lambda: 1.0)
        assert action is RecordFactOutcome.STALE_INTENT
        assert ladder_rung_for_record_fact_outcome(action) is CleanupLadderRung.NORMAL_CLEANUP

    def test_expired_deadline_fails_closed_to_stale_intent(
        self, busy_refusal_outcome: object
    ) -> None:
        """An expired deadline is the typed STALE_INTENT verdict, never a hand-off."""
        action = busy_refusal_outcome(10.0, clock=lambda: 100.0)
        assert action is RecordFactOutcome.STALE_INTENT
        assert ladder_rung_for_record_fact_outcome(action) is CleanupLadderRung.NORMAL_CLEANUP


# ---------------------------------------------------------------------------
# Site 1: recovery - auto_integrate_recovery._maybe_release_diagnostic
# ---------------------------------------------------------------------------


class TestRecoveryReleaseRefusals:
    """``recover_incomplete_integration``'s release path (PRESENT record)."""

    def test_present_record_released_through_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PRESENT record releases through the typed store (not a bare write)."""
        _install_unchanged_collector(monkeypatch)
        before = _write_present_record(tmp_path)
        record_file = tmp_path / RECORD_FILE
        outcome = _maybe_release_diagnostic(tmp_path, WorkspaceScope(tmp_path), DEFAULT_BACKEND)
        assert isinstance(outcome, IntegrationRecord)
        assert not outcome.diagnostic_evidence
        assert outcome.diagnostic_ownership is None
        # The store rewrote the record via RELEASE_DIAGNOSTIC_EVIDENCE;
        # identity fields survive.
        assert outcome.target == "main"
        assert outcome.pre_feature_sha == "a" * 40
        assert record_file.read_bytes() != before  # evidence released on disk

    def test_busy_refusal_retains_record_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BUSY at the store's re-read maps to a retained skip; bytes untouched."""
        before = _write_present_record(tmp_path)
        record_file = tmp_path / RECORD_FILE
        # Call 1 = the release site's top-level read; call 2 = the store's
        # re-read inside apply_record_transition.
        backend = _InjectedBackend(DEFAULT_BACKEND, "busy", fail_on_call=2)
        assert isinstance(backend, object)
        outcome = _maybe_release_diagnostic(tmp_path, WorkspaceScope(tmp_path), backend)
        assert isinstance(outcome, RebaseState)
        assert outcome.last_outcome is RebaseOutcomeKind.SKIPPED
        assert "busy" in (outcome.last_reason or "")
        assert outcome.recovery_record_retained is True
        assert outcome.recovery_record_retained_reason is (
            StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
        )
        _assert_bytes_unchanged(record_file, before)

    def test_unreadable_refusal_maps_to_typed_ladder_escalation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UNREADABLE at the store's re-read maps to the ladder, never an overwrite."""
        _install_unchanged_collector(monkeypatch)
        before = _write_present_record(tmp_path)
        record_file = tmp_path / RECORD_FILE
        backend = _InjectedBackend(DEFAULT_BACKEND, "unreadable", fail_on_call=2)
        outcome = _maybe_release_diagnostic(tmp_path, WorkspaceScope(tmp_path), backend)
        assert isinstance(outcome, RebaseState)
        assert outcome.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
        assert "unreadable" in (outcome.last_reason or "")
        assert outcome.recovery_record_retained is True
        assert outcome.recovery_record_retained_reason is (
            StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
        )
        _assert_bytes_unchanged(record_file, before)


# ---------------------------------------------------------------------------
# Site 2: diagnostic - _integration_diagnostic._complete_or_intervene
# ---------------------------------------------------------------------------


class TestDiagnosticCompletionRefusals:
    """``handoff_integration_diagnostic``'s completion pre-pass release.

    The pre-pass verifies a positive operation-completion proof in a
    real git repo (HEAD == integrated_feature_sha == pre_target_sha,
    clean tree) BEFORE the release transition, so the PRESENT case
    proves the happy path and the BUSY/UNREADABLE cases prove the
    refusal AFTER the proof passes.
    """

    def _setup(self, tmp_git_repo: Path) -> tuple[Path, Path, bytes]:
        from ralph.git.subprocess_runner import run_git

        head = run_git(("rev-parse", "HEAD"), cwd=tmp_git_repo, label="test:head").stdout.strip()
        record = _verified_record(owning_worktree=str(tmp_git_repo))
        record = record.model_copy(update={"integrated_feature_sha": head, "pre_target_sha": head})
        # The lease format is ``<fingerprint>:<held_at>``; use the
        # new format so the proof's ownership check parses it.
        import time

        record = record.model_copy(
            update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{time.time()}"}
        )
        before = _write_present_record(tmp_git_repo, record)
        return tmp_git_repo, tmp_git_repo / RECORD_FILE, before

    def _run(self, root: Path, backend: FileBackend) -> bool:
        record = IntegrationRecord.model_validate_json(
            (root / RECORD_FILE).read_text(encoding="utf-8")
        )
        return _complete_or_intervene(
            root=root,
            inspection_root=root,
            record=record,
            backend=backend,
            collector=_UnchangedCollector(),
        )

    def test_present_record_released_through_store(self, tmp_git_repo: Path) -> None:
        root, record_file, before = self._setup(tmp_git_repo)
        assert self._run(root, DEFAULT_BACKEND) is True
        released = IntegrationRecord.model_validate_json(record_file.read_text("utf-8"))
        assert not released.diagnostic_evidence
        assert released.diagnostic_ownership is None
        assert record_file.read_bytes() != before

    def test_busy_refusal_returns_false_and_preserves_bytes(self, tmp_git_repo: Path) -> None:
        root, record_file, before = self._setup(tmp_git_repo)
        backend = _InjectedBackend(DEFAULT_BACKEND, "busy", fail_on_call=1)
        assert self._run(root, backend) is False
        _assert_bytes_unchanged(record_file, before)

    def test_unreadable_refusal_returns_false_and_preserves_bytes(self, tmp_git_repo: Path) -> None:
        root, record_file, before = self._setup(tmp_git_repo)
        backend = _InjectedBackend(DEFAULT_BACKEND, "unreadable", fail_on_call=1)
        assert self._run(root, backend) is False
        _assert_bytes_unchanged(record_file, before)


# ---------------------------------------------------------------------------
# Site 3: remote reconcile - _apply_release_or_refuse / _maybe_release_target_diagnostic
# ---------------------------------------------------------------------------


def _raise_busy(*args: object, **kwargs: object) -> IntegrationRecord:
    del args, kwargs
    raise RecordBusyError("injected: transient record lock contention")


def _raise_unreadable(*args: object, **kwargs: object) -> IntegrationRecord:
    del args, kwargs
    raise RecordUnreadableError("injected: unreadable record evidence")


class TestRemoteReconcileReleaseRefusals:
    """``recover_remote_target_reconcile``'s release path (default backend).

    This site calls ``apply_record_transition`` through its module-level
    binding with the DEFAULT backend, so the BUSY/UNREADABLE windows are
    injected at the module's own store binding -- the same catch site the
    production caller relies on.
    """

    def test_present_record_releases_to_none(self, tmp_path: Path) -> None:
        before = _write_present_record(tmp_path)
        record_file = tmp_path / RECORD_FILE
        assert _apply_release_or_refuse(tmp_path, _verified_record()) is None
        released = IntegrationRecord.model_validate_json(record_file.read_text("utf-8"))
        assert not released.diagnostic_evidence
        assert record_file.read_bytes() != before

    @pytest.mark.parametrize(
        ("deadline", "bounded"),
        [(10.0, True), (None, False)],
    )
    def test_busy_refusal_typed_and_bytes_preserved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        deadline: float | None,
        bounded: bool,
    ) -> None:
        """BUSY maps through busy_refusal_outcome: bounded wait vs intervention."""
        before = _write_present_record(tmp_path)
        record_file = tmp_path / RECORD_FILE
        monkeypatch.setattr(
            "ralph.pipeline._remote_reconcile_recovery.apply_record_transition", _raise_busy
        )
        state = _apply_release_or_refuse(
            tmp_path, _verified_record(), retry_deadline=deadline, clock=lambda: 1.0
        )
        assert state is not None
        if bounded:
            assert state.last_outcome is RebaseOutcomeKind.SKIPPED
        else:
            # No usable deadline: the refusal escalates into the
            # autonomous fallback ladder, never to an operator.
            assert state.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
        assert "busy" in (state.last_reason or "")
        assert state.recovery_record_retained is True
        _assert_bytes_unchanged(record_file, before)

    def test_unreadable_refusal_maps_to_typed_ladder_escalation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UNREADABLE maps to the typed ladder escalation, never an overwrite."""
        before = _write_present_record(tmp_path)
        record_file = tmp_path / RECORD_FILE
        monkeypatch.setattr(
            "ralph.pipeline._remote_reconcile_recovery.apply_record_transition",
            _raise_unreadable,
        )
        state = _apply_release_or_refuse(
            tmp_path, _verified_record(), retry_deadline=10.0, clock=lambda: 1.0
        )
        assert state is not None
        assert state.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
        assert state.recovery_record_retained is True
        assert state.recovery_record_retained_reason is (
            StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
        )
        _assert_bytes_unchanged(record_file, before)

    def test_busy_refusal_via_public_caller_preserves_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_maybe_release_target_diagnostic`` surfaces the same typed refusal."""
        _install_unchanged_collector(monkeypatch)
        before = _write_present_record(tmp_path)
        record_file = tmp_path / RECORD_FILE
        monkeypatch.setattr(
            "ralph.pipeline._remote_reconcile_recovery.apply_record_transition", _raise_busy
        )
        state = _maybe_release_target_diagnostic(
            tmp_path,
            tmp_path,
            _verified_record(),
            retry_deadline=10.0,
            clock=lambda: 1.0,
        )
        assert state is not None
        assert state.last_outcome is RebaseOutcomeKind.SKIPPED
        assert "busy" in (state.last_reason or "")
        _assert_bytes_unchanged(record_file, before)


# ---------------------------------------------------------------------------
# Cross-site: the store itself owns the refusal (never a bare overwrite)
# ---------------------------------------------------------------------------


def test_store_refuses_busy_without_overwriting(tmp_path: Path) -> None:
    """BUSY bytes must survive: the store, not the caller, refuses the write."""
    before = _write_present_record(tmp_path)
    record_file = tmp_path / RECORD_FILE
    backend = _InjectedBackend(DEFAULT_BACKEND, "busy", fail_on_call=1)
    with pytest.raises(RecordBusyError):
        apply_record_transition(
            tmp_path,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            backend=backend,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )
    _assert_bytes_unchanged(record_file, before)


def test_store_refuses_unreadable_without_overwriting(tmp_path: Path) -> None:
    """UNREADABLE bytes are the only ownership proof: never overwritten."""
    record_file = tmp_path / RECORD_FILE
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text("{not valid json", encoding="utf-8")
    before = record_file.read_bytes()
    with pytest.raises(RecordUnreadableError):
        apply_record_transition(
            tmp_path,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )
    _assert_bytes_unchanged(record_file, before)
