"""AC-5 acceptance: containment releases ALL integration state and lets the run continue.

Once every feature commit is already inside the integration target there is
no unresolved work left to protect: the durable record, the retry counter,
the unresolved/retained latches and the exhausted-resolver block are all
stale history, and keeping any of them blocks ordinary dispatch against a
repository that is already correct. The single containment verdict
(:func:`evaluate_integration_containment`) is the only thing allowed to
decide that, and git -- not the record -- is the source of the answer.

Pure fakes: no real ``git`` subprocess, no ``time.sleep``. The record seam
is driven through an injected :class:`FileBackend`; the git seams are
monkeypatched at their defining modules.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.git import merge as merge_module
from ralph.git import merge_obstructions, operations, remote_push
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    apply_containment_release,
    clear_record_on_containment,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_resolution_state import (
    contained_land,
    containment_release,
    evaluate_integration_containment,
    preserve_unresolved_resolution_state,
    reconcile_stale_unresolved_state,
)
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

_ROOT = Path("/virtual-workspace")
_TARGET = "main"
_FEATURE_SHA = "f" * 40
_TARGET_SHA = "a" * 40


class _RecordBackend(FileBackend):
    """In-memory durable publication boundary with observable mutations."""

    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path].decode(encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path]

    def write_bytes(self, path: Path, content: bytes) -> None:
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
        else:
            del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def _record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrated",
        target=_TARGET,
        pre_feature_sha="e" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha=_FEATURE_SHA,
    )


def _latched_state() -> RebaseState:
    """A state carrying every latch a contained integration may have left behind."""
    return RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_target=_TARGET,
        integration_retry_attempt=3,
        unresolved_integration_carried=True,
        legacy_checkpoint_blocked=True,
        resolution_exhausted=True,
        resolution_exhaustion_reason="resolver chain exhausted",
        recovery_record_retained=True,
        recovery_record_retained_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
    )


def _fake_git(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ancestor: bool | None,
    operation_in_flight: bool = False,
) -> None:
    """Answer the git seams the containment verdict reads."""
    monkeypatch.setattr(operations, "get_head_sha", lambda _root: _FEATURE_SHA)
    monkeypatch.setattr(merge_module, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(merge_obstructions, "ancestry_state", lambda _r, _a, _d: ancestor)
    from ralph.git import merge as merge_probe
    from ralph.git.rebase import rebase as rebase_module

    monkeypatch.setattr(rebase_module, "rebase_in_progress", lambda _root=None: operation_in_flight)
    monkeypatch.setattr(
        merge_probe,
        "merge_state",
        lambda _root: (
            merge_probe.MERGE_STATE_IN_PROGRESS
            if operation_in_flight
            else merge_probe.MERGE_STATE_NONE
        ),
    )


def _record_file() -> Path:
    return _ROOT / ".agent" / "auto_integrate_in_progress.json"


# --- 1. containment releases every latch and the durable record ---


def test_contained_feature_clears_record_and_every_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feature fully contained in the target releases the record and all latches."""
    backend = _RecordBackend()
    write_record(_ROOT, _record(), backend=backend)
    _fake_git(monkeypatch, ancestor=True)

    verdict = evaluate_integration_containment(_ROOT, _TARGET)
    released = apply_containment_release(_latched_state(), _ROOT, verdict, backend=backend)

    assert verdict.contained is True
    assert verdict.feature_sha == _FEATURE_SHA
    assert verdict.target_sha == _TARGET_SHA
    assert _record_file() not in backend.files
    assert released.last_outcome is None
    assert released.integration_retry_attempt == 0
    assert released.unresolved_integration_carried is False
    assert released.legacy_checkpoint_blocked is False
    assert released.recovery_record_retained is False
    assert released.recovery_record_retained_reason is None
    assert released.resolution_exhausted is False
    assert released.integration_unresolved is False


def test_containment_release_keeps_operator_strategy_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing a retry latch preserves the strategy history the operator chose."""
    _fake_git(monkeypatch, ancestor=True)
    state = _latched_state().model_copy(
        update={"resolution_exhausted": False, "conflict_strategy_index": 2}
    )

    released = containment_release(_ROOT, _TARGET, state)

    assert released.resolution_exhausted is False
    assert released.conflict_strategy_index == 2
    assert released.integration_retry_attempt == 0


def test_clear_record_on_containment_refuses_without_the_verdict() -> None:
    """The clear-on-contained capability never unlinks an unproven record."""
    backend = _RecordBackend()
    write_record(_ROOT, _record(), backend=backend)
    unproven = evaluate_integration_containment(None, _TARGET)

    assert clear_record_on_containment(_ROOT, unproven, backend=backend) is False
    assert _record_file() in backend.files


# --- 2. landing is idempotent ---


def test_land_twice_is_a_noop_the_second_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Landing an already-contained target changes nothing and stays released."""
    _fake_git(monkeypatch, ancestor=True)
    write_record(tmp_path, _record())

    first = contained_land(tmp_path, _TARGET)
    second = contained_land(tmp_path, _TARGET)

    assert first is not None
    assert first.fast_forwarded is True
    assert first.last_outcome is RebaseOutcomeKind.FAST_FORWARDED
    assert first.integration_unresolved is False
    assert first == second
    assert read_record(tmp_path) is None


def test_land_short_circuits_before_any_rebase_or_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A contained target makes the seam succeed without rebasing or merging."""
    from ralph.pipeline import auto_integrate

    _fake_git(monkeypatch, ancestor=True)
    write_record(tmp_path, _record())

    def _unreachable(*_a: object, **_kw: object) -> object:
        raise AssertionError("a contained target must not rebase, merge, or move a ref")

    monkeypatch.setattr(auto_integrate, "post_attempt_verify", lambda *_a, **_kw: None)
    monkeypatch.setattr(auto_integrate, "_run_rebase_or_merge", _unreachable)
    monkeypatch.setattr(auto_integrate, "_create_rebase_backup_ref", _unreachable)
    monkeypatch.setattr(auto_integrate, "_fast_forward_target", _unreachable)

    outcome, retry = auto_integrate._integrate_once_owned(
        SimpleNamespace(), tmp_path, _TARGET, None
    )

    assert retry is False
    assert outcome.fast_forwarded is True
    assert outcome.last_outcome is RebaseOutcomeKind.FAST_FORWARDED
    assert outcome.integration_unresolved is False
    assert read_record(tmp_path) is None


def test_land_without_containment_falls_through_to_the_real_seam(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No containment proof means no short-circuit: the seam runs for real."""
    from ralph.pipeline import auto_integrate

    _fake_git(monkeypatch, ancestor=False)
    monkeypatch.setattr(auto_integrate, "post_attempt_verify", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        auto_integrate,
        "_run_rebase_or_merge",
        lambda *_a, **_kw: SimpleNamespace(short_circuit=None),
    )

    assert contained_land(tmp_path, _TARGET) is None


# --- 3. publish is idempotent ---


def test_publish_skips_the_push_when_local_equals_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal refs satisfy the postcondition, so no push subprocess is issued."""
    issued: list[str] = []
    monkeypatch.setattr(remote_push, "_list_remotes", lambda _root: ["origin"])
    monkeypatch.setattr(remote_push, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(remote_push, "_remote_ref_sha", lambda _root, _remote, _branch: _TARGET_SHA)
    monkeypatch.setattr(
        remote_push,
        "_push_to_remote",
        lambda *_a, **_kw: issued.append("push") or (True, ""),
    )

    result = remote_push.push_branch_to_single_remote(
        _ROOT, _TARGET, remote="origin", timeout_seconds=5.0
    )

    assert issued == []
    assert result.success is True
    assert result.detail == remote_push.UP_TO_DATE_DETAIL
    assert result.summary == f"{_TARGET} already published to origin"


@pytest.mark.parametrize(
    "remote_sha",
    [None, "d" * 40],
    ids=["remote_branch_absent", "remote_behind_local"],
)
def test_publish_still_pushes_whenever_the_refs_differ(
    monkeypatch: pytest.MonkeyPatch, remote_sha: str | None
) -> None:
    """Only equality skips: an absent or moved remote ref must still be pushed."""
    issued: list[str] = []
    monkeypatch.setattr(remote_push, "_list_remotes", lambda _root: ["origin"])
    monkeypatch.setattr(remote_push, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(remote_push, "_remote_ref_sha", lambda _root, _remote, _branch: remote_sha)
    monkeypatch.setattr(
        remote_push,
        "_push_to_remote",
        lambda *_a, **_kw: issued.append("push") or (True, ""),
    )

    result = remote_push.push_branch_to_single_remote(
        _ROOT, _TARGET, remote="origin", timeout_seconds=5.0
    )

    assert issued == ["push"]
    assert result.success is True
    assert result.summary == f"pushed {_TARGET} to origin"


# --- 4. regression guard: do not over-release ---


def test_uncontained_feature_retains_the_record_and_every_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feature with commits beyond the target keeps all of its evidence."""
    backend = _RecordBackend()
    write_record(_ROOT, _record(), backend=backend)
    _fake_git(monkeypatch, ancestor=False)
    state = _latched_state()

    verdict = evaluate_integration_containment(_ROOT, _TARGET)
    released = apply_containment_release(state, _ROOT, verdict, backend=backend)

    assert verdict.contained is False
    assert _record_file() in backend.files
    assert released is state
    assert released.integration_unresolved is True


def test_unreadable_git_keeps_every_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unanswerable ancestry query is never containment."""
    _fake_git(monkeypatch, ancestor=None)

    verdict = evaluate_integration_containment(_ROOT, _TARGET)

    assert verdict.contained is False
    assert containment_release(_ROOT, _TARGET, _latched_state()).integration_unresolved is True


def test_reconcile_defers_to_git_when_a_repository_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean inspection no longer outranks a live git answer."""
    _fake_git(monkeypatch, ancestor=False)
    state = _latched_state()

    reconciled = reconcile_stale_unresolved_state(state, root=_ROOT, target=_TARGET)

    assert reconciled.integration_unresolved is True
    assert reconciled.integration_retry_attempt == 3


def test_reconcile_without_a_repository_releases_on_the_caller_inspection() -> None:
    """Legacy callers pass no repository; their clean inspection is the proof."""
    reconciled = reconcile_stale_unresolved_state(_latched_state())

    assert reconciled.integration_unresolved is False
    assert reconciled.integration_retry_attempt == 0
    assert reconciled.recovery_record_retained is False


# --- 5. a landed fast-forward releases through the same verdict ---


def test_landed_outcome_releases_every_latch_not_just_the_retry_counter() -> None:
    """The old branch cleared only the retry counter; the verdict clears all of it."""
    landed = _latched_state().model_copy(update={"fast_forwarded": True})

    preserved = preserve_unresolved_resolution_state(landed, prior=_latched_state())

    assert preserved is not None
    assert preserved.integration_retry_attempt == 0
    assert preserved.recovery_record_retained is False
    assert preserved.unresolved_integration_carried is False
    assert preserved.resolution_exhausted is False
    assert preserved.integration_unresolved is False
