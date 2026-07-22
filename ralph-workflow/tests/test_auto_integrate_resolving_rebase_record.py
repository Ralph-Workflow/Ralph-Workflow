"""The in-flight rebase resolution must be durably recorded before it starts.

``resolving_rebase`` is the ONLY thing that lets startup recovery tell an
interrupted conflict resolution apart from an ordinary crashed rebase
(:mod:`ralph.pipeline.auto_integrate_recovery` branches on it to emit a
distinct warning). A resolution session runs for as long as an agent
takes, so the window in which that distinction matters is the widest
window auto-integration has.

:func:`ralph.pipeline.auto_integrate_record.set_resolving_rebase` used to
swallow every record I/O error and return ``None``, and
``_resolve_conflicted_rebase`` started the resolver regardless -- so a
run killed mid-resolution left a record still saying ``false`` and the
operator lost the warning entirely. These tests pin the two halves of the
fix: the writer REPORTS whether the durable state agrees with the caller,
and the caller refuses to start a resolution it could not record, taking
the pre-existing abort-then-endpoint-merge path instead.

Every seam is injected, so nothing here launches git or touches a
repository beyond one JSON file under ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.merge import MergeResult
from ralph.git.rebase.rebase import RebaseConflicts
from ralph.pipeline import auto_integrate_rebase_merge as merge_module
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    set_resolving_rebase,
    write_record,
)

if TYPE_CHECKING:
    import pytest

    from ralph.pipeline.conflict_resolution import RebaseStop

_TARGET = "main"


def _integrating_record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target=_TARGET,
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
    )


def test_set_resolving_rebase_reports_a_persisted_flag(tmp_path: Path) -> None:
    """The happy path both writes the flag AND says that it did."""
    write_record(tmp_path, _integrating_record())

    assert set_resolving_rebase(tmp_path, True) is True

    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.resolving_rebase is True


def test_set_resolving_rebase_reports_success_when_there_is_no_record(
    tmp_path: Path,
) -> None:
    """No record means recovery has nothing to act on, so nothing is lost.

    A missing record -- which is also how :func:`read_record` reports a
    corrupt one -- is not a durability gap: recovery reads the same
    absent record and takes no action either way, so there is no
    interrupted-resolution warning that failing here would preserve.
    """
    assert set_resolving_rebase(tmp_path, True) is True
    assert read_record(tmp_path) is None


def test_set_resolving_rebase_reports_failure_when_a_present_record_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An existing-but-unreadable record is not the same as no record.

    :func:`read_record` collapses absent, corrupt and transiently
    unreadable into one ``None``, so the file's EXISTENCE is what
    separates "nothing to record" from "something is there and we could
    not see it". Reporting the second as a persisted flag would reopen
    exactly the gap this guard closes: the record on disk still says
    ``false`` while the caller believes it says ``true``.
    """
    write_record(tmp_path, _integrating_record())
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_record.read_record", lambda _root: None
    )

    assert set_resolving_rebase(tmp_path, True) is False


def test_set_resolving_rebase_reports_failure_when_the_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record that could not be updated must be reported, not swallowed."""
    write_record(tmp_path, _integrating_record())

    def _boom(_root: Path, _record: IntegrationRecord) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_record.write_record", _boom
    )

    assert set_resolving_rebase(tmp_path, True) is False

    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.resolving_rebase is False


def _install_fallback_seams(
    monkeypatch: pytest.MonkeyPatch, resolver_calls: list[str]
) -> None:
    """Fake every git seam ``run_rebase_or_merge`` reaches after a conflict."""
    monkeypatch.setattr(
        merge_module,
        "rebase_onto",
        lambda _target, repo_root: RebaseConflicts(files=["src/alpha.py"]),
    )
    monkeypatch.setattr(merge_module, "abort_rebase", lambda repo_root: None)
    monkeypatch.setattr(merge_module, "rebase_in_progress", lambda _root: False)
    monkeypatch.setattr(
        merge_module,
        "endpoint_merge_with_resolution",
        lambda _root, _target, _resolver: MergeResult(outcome="success"),
    )

    def _never(_root: Path, _target: str, _resolver: object) -> bool:
        resolver_calls.append("resolve_rebase_in_progress")
        return True

    monkeypatch.setattr(merge_module, "resolve_rebase_in_progress", _never)


def test_an_unrecordable_resolution_is_never_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed: no durable flag, no long-lived resolution session.

    Starting the resolver anyway would leave a paused rebase under an
    agent whose crash the record could not describe. The fallback aborts
    the rebase here and now, while this process is still alive to do it.
    """
    resolver_calls: list[str] = []
    _install_fallback_seams(monkeypatch, resolver_calls)
    monkeypatch.setattr(
        merge_module, "set_resolving_rebase", lambda _root, _resolving: False
    )
    # The paused rebase is real until the fallback aborts it, so the
    # abort must be OBSERVED rather than assumed: "handed to the
    # fallback" is only safe because the fallback tears the rebase down
    # while this process is still alive.
    aborted: list[Path] = []

    def _abort(repo_root: Path) -> None:
        aborted.append(repo_root)

    monkeypatch.setattr(
        merge_module, "rebase_in_progress", lambda _root: not aborted
    )
    monkeypatch.setattr(merge_module, "abort_rebase", _abort)

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


def test_a_recordable_resolution_is_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard is the write's ANSWER, not the presence of the guard.

    Companion to the fail-closed case: with the flag persisted, the same
    conflicted rebase reaches the resolution loop and never touches the
    endpoint merge.
    """
    resolver_calls: list[str] = []
    _install_fallback_seams(monkeypatch, resolver_calls)
    flags: list[bool] = []
    monkeypatch.setattr(
        merge_module,
        "set_resolving_rebase",
        lambda _root, resolving: (flags.append(resolving), True)[1],
    )

    result = merge_module.run_rebase_or_merge(
        tmp_path,
        _TARGET,
        None,
        rebase_stop_resolver=lambda _root, _target, _stop: True,
    )

    assert resolver_calls == ["resolve_rebase_in_progress"]
    assert result.merge_attempted is False
    assert flags == [True, False]
