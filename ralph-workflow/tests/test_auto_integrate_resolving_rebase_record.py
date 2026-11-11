from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.conflict_resolution_config import ConflictResolutionConfig
from ralph.git.merge import MergeResult
from ralph.git.rebase.rebase import RebaseConflicts
from ralph.pipeline import auto_integrate_rebase_merge as merge_module
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    set_resolving_rebase,
    write_record,
)
from ralph.pipeline.conflict_resolution import RebaseStop

if TYPE_CHECKING:
    import pytest

    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

_TARGET = "main"


def _unreadable_record(_root: Path) -> None:
    return None


def _persisted_flag(_root: Path, _resolving: bool) -> bool:
    return True


def _integrating_record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target=_TARGET,
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
    )


def test_set_resolving_rebase_reports_a_persisted_flag(tmp_path: Path) -> None:
    write_record(tmp_path, _integrating_record())

    assert set_resolving_rebase(tmp_path, True) is True

    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.resolving_rebase is True


def test_set_resolving_rebase_reports_success_when_there_is_no_record(
    tmp_path: Path,
) -> None:
    assert set_resolving_rebase(tmp_path, True) is True
    assert read_record(tmp_path) is None


def test_set_resolving_rebase_reports_failure_when_a_present_record_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_record(tmp_path, _integrating_record())
    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", _unreadable_record)

    assert set_resolving_rebase(tmp_path, True) is False


def test_set_resolving_rebase_reports_failure_when_the_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_record(tmp_path, _integrating_record())

    def _boom(_root: Path, _record: IntegrationRecord) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.write_record", _boom)

    assert set_resolving_rebase(tmp_path, True) is False

    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.resolving_rebase is False


def _install_fallback_seams(monkeypatch: pytest.MonkeyPatch, resolver_calls: list[str]) -> None:

    def no_range_reason(_root: Path, _target: str) -> None:
        return None

    def conflicted(_target: str, repo_root: Path) -> RebaseConflicts:
        return RebaseConflicts(files=["src/alpha.py"])

    def no_active_rebase(_root: Path) -> bool:
        return False

    def merge_success(_root: Path, _target: str, _resolver: object) -> MergeResult:
        return MergeResult(outcome="success")

    monkeypatch.setattr(merge_module, "_range_routing_reason", no_range_reason)
    monkeypatch.setattr(merge_module, "rebase_onto", conflicted)
    monkeypatch.setattr(merge_module, "abort_rebase_discarding_progress", _unreadable_record)
    monkeypatch.setattr(merge_module, "rebase_in_progress", no_active_rebase)
    monkeypatch.setattr(merge_module, "endpoint_merge_with_resolution", merge_success)

    def _never(_root: Path, _target: str, _resolver: object, **_kwargs: object) -> bool:
        resolver_calls.append("resolve_rebase_in_progress")
        return True

    monkeypatch.setattr(merge_module, "resolve_rebase_in_progress", _never)


def test_an_unrecordable_resolution_is_never_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resolver_calls: list[str] = []
    _install_fallback_seams(monkeypatch, resolver_calls)

    def cannot_record(_root: Path, _resolving: bool) -> bool:
        return False

    monkeypatch.setattr(merge_module, "set_resolving_rebase", cannot_record)
    aborted: list[Path] = []

    def _abort(repo_root: Path) -> None:
        aborted.append(repo_root)

    def still_active(_root: Path) -> bool:
        return not aborted

    monkeypatch.setattr(merge_module, "rebase_in_progress", still_active)
    monkeypatch.setattr(merge_module, "abort_rebase_discarding_progress", _abort)

    def _stop_resolver(_root: Path, _target: str, _stop: RebaseStop) -> bool:
        raise AssertionError("the stop resolver must not run")

    result = merge_module.run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        rebase_stop_resolver=_stop_resolver,
    )

    assert resolver_calls == []
    assert aborted == [tmp_path]
    assert result.merge_attempted is True
    assert result.merge_outcome is not None
    assert result.merge_outcome.outcome == "success"


def test_conflict_resolution_regression_normal_rebase_binds_one_session_to_all_three_argument_resolver_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resolver_calls: list[str] = []
    _install_fallback_seams(monkeypatch, resolver_calls)
    sessions: list[ResolutionSession] = []
    monkeypatch.setattr(merge_module, "set_resolving_rebase", _persisted_flag)

    def resolve_with_session(
        root: Path,
        target: str,
        callback: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> ResolutionOutcome | bool:
        sessions.append(session)
        return callback(
            root,
            target,
            RebaseStop(
                sha="abc123",
                subject="feature",
                conflicted_files=("src/alpha.py",),
                stop_index=1,
                stop_cap=10,
            ),
        )

    monkeypatch.setattr(merge_module, "resolve_rebase_in_progress", resolve_with_session)

    def resolver(_root: Path, _target: str, _stop: RebaseStop) -> bool:
        return True

    result = merge_module.run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        rebase_stop_resolver=resolver,
        conflict_resolution_config=ConflictResolutionConfig(total_resolution_cap_seconds=60.0),
    )

    assert result.merge_attempted is False
    assert len(sessions) == 1
    assert sessions[0].total_resolution_cap_seconds == 60.0


def test_a_recordable_resolution_is_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resolver_calls: list[str] = []
    _install_fallback_seams(monkeypatch, resolver_calls)
    flags: list[bool] = []

    def record_flag(_root: Path, resolving: bool) -> bool:
        flags.append(resolving)
        return True

    monkeypatch.setattr(merge_module, "set_resolving_rebase", record_flag)

    result = merge_module.run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        rebase_stop_resolver=lambda _root, _target, _stop: True,
    )

    assert resolver_calls == ["resolve_rebase_in_progress"]
    assert result.merge_attempted is False
    assert flags == [True, False]


def test_resolver_chain_exhaustion_retains_progress_for_next_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fallback_seams(monkeypatch, [])
    monkeypatch.setattr(merge_module, "set_resolving_rebase", _persisted_flag)

    def _exhausted(
        _root: Path, _target: str, _resolver: object, *, session: ResolutionSession
    ) -> bool:
        session.exhaustion_reason = "RESOLUTION_CHAIN_EXHAUSTED: src/alpha.py"
        return False

    monkeypatch.setattr(merge_module, "resolve_rebase_in_progress", _exhausted)

    result = merge_module.run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        rebase_stop_resolver=lambda _root, _target, _stop: False,
    )

    assert result.merge_attempted is False
    assert result.short_circuit is not None and result.short_circuit.recovery_record_retained
    assert "RESOLUTION_CHAIN_EXHAUSTED" in (result.short_circuit.last_reason or "")
    assert result.merge_outcome is None


def test_an_unparseable_record_is_preserved_without_authorizing_new_resolution(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.auto_integrate_record import record_path

    for content in ("{not json at all", '{"phase": "a-phase-from-the-future"}'):
        (tmp_path / ".agent").mkdir(exist_ok=True)
        record_path(tmp_path).write_text(content, encoding="utf-8")
        assert set_resolving_rebase(tmp_path, True) is False, content
        assert record_path(tmp_path).read_text(encoding="utf-8") == content


def test_an_intact_record_is_never_discarded_by_a_transient_read_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.auto_integrate_record import record_path

    write_record(tmp_path, _integrating_record())
    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", _unreadable_record)

    assert set_resolving_rebase(tmp_path, True) is False
    assert record_path(tmp_path).exists(), "an intact record must survive"
