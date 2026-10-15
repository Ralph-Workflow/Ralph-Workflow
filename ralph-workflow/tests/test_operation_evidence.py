"""Black-box tests for the operation-scoped evidence collector.

The collector is the architectural fix for the linked-worktree
retained-integration reproduction. The tests in this file assert
both the operation-scoped *positive* contract (the right facts are
emitted) and the operation-scoped *negative* contract (the
repository-global reads the production bug relied on are
forbidden).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.operation_evidence import (
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_REBASE_OBJECT_RESOLVABLE,
    InMemoryOperationEvidenceCollector,
    OperationEvidenceCollector,
    OperationFact,
    SubprocessOperationEvidenceCollector,
    ownership_fingerprint,
)

if TYPE_CHECKING:
    import pytest


def _record(**overrides: object) -> IntegrationRecord:
    base: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "f" * 40,
        "pre_target_sha": "0" * 40,
        "resolving_paths": (),
    }
    base.update(overrides)
    return IntegrationRecord.model_validate(base)


def _mock_git(monkeypatch: pytest.MonkeyPatch, root: Path, head_sha: str = "a" * 40) -> Path:
    """Initialize a mock Git repo structure at ``root`` without real subprocess calls.

    Mocks ``run_git`` to return valid git responses so tests remain pure and in-budget.
    """
    root.mkdir(parents=True, exist_ok=True)
    gitdir = root / ".git"
    gitdir.mkdir(parents=True, exist_ok=True)

    def fake_run_git(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if args_t == ("rev-parse", "--verify", "HEAD"):
            return GitRunResult(args=args_t, returncode=0, stdout=f"{head_sha}\n", stderr="")
        if args_t == ("rev-parse", "--absolute-git-dir"):
            return GitRunResult(args=args_t, returncode=0, stdout=f"{gitdir}\n", stderr="")
        if len(args_t) >= 1 and args_t[0] == "ls-files":
            return GitRunResult(args=args_t, returncode=0, stdout="100644 hash 0 file\0", stderr="")
        return GitRunResult(args=args_t, returncode=1, stdout="", stderr="unsupported")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", fake_run_git)
    return root


def test_operation_fact_round_trips_to_evidence_tuple_without_scope() -> None:
    fact = OperationFact(kind="head", value="abc123")
    assert fact.to_evidence_tuple() == ("head", "abc123")


def test_operation_fact_round_trips_to_evidence_tuple_with_scope() -> None:
    fact = OperationFact(
        kind="rebase_state_files",
        value="hash",
        scope="/tmp/repo/.git/worktrees/foo/rebase-merge/git-rebase-todo",
    )
    key, value = fact.to_evidence_tuple()
    assert value == "hash"
    assert key.startswith("rebase_state_files:")
    assert "rebase-merge/git-rebase-todo" in key


def test_subprocess_collector_default_emits_five_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default collector must emit the operation-scoped kinds.

    ``head``, ``worktree_path``, ``index_scope``, and
    ``ownership_fingerprint`` are always present. ``rebase_state_files``
    and ``merge_state_files`` are only present when the operation
    worktree has files in the per-worktree rebase/merge directories.
    """
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record()
    facts = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    kinds = {fact.kind for fact in facts}
    assert {"head", "worktree_path", "index_scope", "ownership_fingerprint"}.issubset(kinds)
    extra = kinds - {"head", "worktree_path", "index_scope", "ownership_fingerprint"}
    assert extra.issubset({"rebase_state_files", "merge_state_files"})


def test_subprocess_collector_never_emits_for_each_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git for-each-ref`` is repository-global; the default collector never calls it.

    The collector is the only component permitted to read Git state
    for diagnostic evidence; the Protocol has no method that returns
    it. Asserting the contract on the value domain (the kinds it
    emits) is the only place a regression test can land, because
    there is no ``method_named_for_each_ref`` to look up.
    """
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record()
    facts = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    # ``for-each-ref`` would surface as a kind labelled with the
    # subcommand name (matching the legacy on-disk shape that
    # produced the production bug). The new collector never emits
    # that kind.
    assert "for-each-ref" not in {fact.kind for fact in facts}
    # The full ``ls-files --stage`` would surface as a kind carrying
    # the entire index digest. The new collector only emits
    # ``index_scope`` (path-restricted) and emits ``absent`` when
    # the scope is empty.
    assert "ls-files" not in {fact.kind for fact in facts}


def test_subprocess_collector_excludes_common_dir_reflog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Branch reflog + ``logs/HEAD`` are common-dir state; the collector must skip them.

    In a linked worktree, the branch reflog and ``logs/HEAD`` live
    under commondir, so any commit in any worktree shifts their
    digest. The default collector's scope is the operation worktree
    only, so a capture from the operation worktree must NEVER
    observe a reflog path.
    """
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record()
    facts = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    for fact in facts:
        if fact.scope is None:
            continue
        assert "/logs/HEAD" not in fact.scope, (
            f"collector must skip commondir logs/HEAD: {fact.scope}"
        )
        assert "/logs/refs/heads/" not in fact.scope, (
            f"collector must skip commondir branch reflog: {fact.scope}"
        )


def test_index_scope_uses_resolving_paths_and_emits_absent_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collector emits ``absent`` for the index scope when the record has none."""
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record(resolving_paths=())
    facts = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    index_facts = [fact for fact in facts if fact.kind == "index_scope"]
    assert len(index_facts) == 1
    assert index_facts[0].value == "absent"


def test_ownership_fingerprint_stable_under_volatile_field_changes() -> None:
    record_a = _record(repair_last_error="error A", merge_commit_repair_attempts=3)
    record_b = _record(repair_last_error="error B", merge_commit_repair_attempts=7)
    assert ownership_fingerprint(record_a) == ownership_fingerprint(record_b)


def test_ownership_fingerprint_changes_when_identity_changes() -> None:
    record_a = _record(target="main")
    record_b = _record(target="develop")
    assert ownership_fingerprint(record_a) != ownership_fingerprint(record_b)


def test_in_memory_collector_returns_supplied_facts(tmp_path: Path) -> None:
    facts = (
        OperationFact(kind="head", value="abc"),
        OperationFact(kind="worktree_path", value=str(tmp_path)),
    )
    collector = InMemoryOperationEvidenceCollector(facts)
    record = _record()
    assert collector.snapshot(tmp_path, record) == facts
    assert collector.verify(tmp_path, record, facts) is True
    assert collector.verify(tmp_path, record, ()) is False


def test_in_memory_collector_honours_protocol(tmp_path: Path) -> None:
    """The in-memory fake must satisfy the collector Protocol contract."""
    collector: OperationEvidenceCollector = InMemoryOperationEvidenceCollector()
    record = _record()
    assert collector.snapshot(tmp_path, record) == ()
    assert collector.verify(tmp_path, record, ()) is True


def test_unreadable_observations_are_stable_under_verify(tmp_path: Path) -> None:
    """An observation that was unreadable at record time stays unverifiable.

    The diagnostic path must not silently disagree on a fact that
    was unreadable when the record was captured. The collector's
    verify treats ``unreadable`` as a stable observation: the
    fingerprint does not rotate, the recovery path is not
    spuriously refused.
    """
    observed = (OperationFact(kind="head", value="unreadable"),)
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="head", value="unreadable"),)
    )
    record = _record()
    assert collector.verify(tmp_path, record, observed) is True


def test_collector_protocol_export() -> None:
    """The collector is exported from the public package surface."""
    from ralph.pipeline import operation_evidence

    assert hasattr(operation_evidence, "OperationEvidenceCollector")
    assert hasattr(operation_evidence, "OperationFact")
    assert hasattr(operation_evidence, "SubprocessOperationEvidenceCollector")
    assert hasattr(operation_evidence, "InMemoryOperationEvidenceCollector")
    assert hasattr(operation_evidence, "_KIND_MERGE_OBJECT_RESOLVABLE")
    assert hasattr(operation_evidence, "_KIND_REBASE_OBJECT_RESOLVABLE")
    assert hasattr(operation_evidence, "ownership_fingerprint")


def test_subprocess_collector_returns_unreadable_when_head_missing(tmp_path: Path) -> None:
    """A non-git root returns ``unreadable`` for HEAD, not a fabricated SHA."""
    record = _record()
    facts = SubprocessOperationEvidenceCollector().snapshot(tmp_path, record)
    head_facts = [fact for fact in facts if fact.kind == "head"]
    assert head_facts
    assert head_facts[0].value == "unreadable"


def test_operation_fact_reachability_kinds_round_trip() -> None:
    merge_fact = OperationFact(kind=_KIND_MERGE_OBJECT_RESOLVABLE, value="True")
    assert merge_fact.to_evidence_tuple() == ("merge_object_resolvable", "True")
    rebase_fact = OperationFact(kind=_KIND_REBASE_OBJECT_RESOLVABLE, value="False")
    assert rebase_fact.to_evidence_tuple() == ("rebase_object_resolvable", "False")


def test_in_memory_collector_reachability_support(tmp_path: Path) -> None:
    collector = InMemoryOperationEvidenceCollector(
        reachability={"MERGE_HEAD": True, "REBASE_HEAD": False}
    )
    record = _record()
    facts = collector.snapshot(tmp_path, record)
    kinds = {f.kind: f.value for f in facts}
    assert kinds[_KIND_MERGE_OBJECT_RESOLVABLE] == "True"
    assert kinds[_KIND_REBASE_OBJECT_RESOLVABLE] == "False"


def test_in_memory_collector_verify_treats_false_reachability_as_gap(tmp_path: Path) -> None:
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind=_KIND_MERGE_OBJECT_RESOLVABLE, value="False"),)
    )
    record = _record()
    assert (
        collector.verify(
            tmp_path, record, (OperationFact(kind=_KIND_MERGE_OBJECT_RESOLVABLE, value="False"),)
        )
        is False
    )


def test_subprocess_collector_reachability_mock_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    gitdir = repo / ".git"
    (gitdir / "MERGE_HEAD").write_text("b" * 40 + "\n", encoding="utf-8")

    def fake_run_git(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if args_t == ("rev-parse", "--verify", "HEAD"):
            return GitRunResult(args=args_t, returncode=0, stdout="a" * 40 + "\n", stderr="")
        if args_t == ("rev-parse", "--absolute-git-dir"):
            return GitRunResult(args=args_t, returncode=0, stdout=f"{gitdir}\n", stderr="")
        if args_t == ("cat-file", "-e", "b" * 40):
            return GitRunResult(args=args_t, returncode=0, stdout="", stderr="")
        if args_t == ("cat-file", "-e", "0" * 40):
            return GitRunResult(args=args_t, returncode=1, stdout="", stderr="missing")
        return GitRunResult(args=args_t, returncode=1, stdout="", stderr="unsupported")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", fake_run_git)

    record = _record(resolving_merge=True)
    facts = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    merge_facts = [f for f in facts if f.kind == _KIND_MERGE_OBJECT_RESOLVABLE]
    assert len(merge_facts) == 1
    assert merge_facts[0].value == "True"

    # Now with unreachable SHA
    (gitdir / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    facts_unreachable = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    merge_facts_unreachable = [
        f for f in facts_unreachable if f.kind == _KIND_MERGE_OBJECT_RESOLVABLE
    ]
    assert len(merge_facts_unreachable) == 1
    assert merge_facts_unreachable[0].value == "False"


def test_subprocess_collector_reachability_unreadable_when_marker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record(resolving_merge=True)
    facts = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    merge_facts = [f for f in facts if f.kind == _KIND_MERGE_OBJECT_RESOLVABLE]
    assert len(merge_facts) == 1
    assert merge_facts[0].value == "unreadable"


def test_subprocess_collector_verify_treats_false_reachability_as_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record()
    observed = (OperationFact(kind=_KIND_MERGE_OBJECT_RESOLVABLE, value="False"),)
    collector = SubprocessOperationEvidenceCollector()
    assert collector.verify(repo, record, observed) is False
