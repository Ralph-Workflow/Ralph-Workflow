from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.operation_evidence import (
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_REBASE_OBJECT_RESOLVABLE,
    EvidenceVerdict,
    InMemoryOperationEvidenceCollector,
    OperationEvidenceCollector,
    OperationFact,
    SubprocessOperationEvidenceCollector,
    ownership_fingerprint,
)


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
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record()
    facts = SubprocessOperationEvidenceCollector().snapshot(repo, record)
    assert "for-each-ref" not in {fact.kind for fact in facts}
    assert "ls-files" not in {fact.kind for fact in facts}


def test_subprocess_collector_excludes_common_dir_reflog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    collector: OperationEvidenceCollector = InMemoryOperationEvidenceCollector()
    record = _record()
    assert collector.snapshot(tmp_path, record) == ()
    assert collector.verify(tmp_path, record, ()) is True


def test_unreadable_observations_are_stable_under_verify(tmp_path: Path) -> None:
    observed = (OperationFact(kind="head", value="unreadable"),)
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="head", value="unreadable"),)
    )
    record = _record()
    assert collector.verify(tmp_path, record, observed) is True


def test_collector_protocol_export() -> None:
    from ralph.pipeline import operation_evidence

    assert hasattr(operation_evidence, "EvidenceVerdict")
    assert hasattr(operation_evidence, "OperationEvidenceCollector")
    assert hasattr(operation_evidence, "OperationFact")
    assert hasattr(operation_evidence, "SubprocessOperationEvidenceCollector")
    assert hasattr(operation_evidence, "InMemoryOperationEvidenceCollector")
    assert hasattr(operation_evidence, "_KIND_MERGE_OBJECT_RESOLVABLE")
    assert hasattr(operation_evidence, "_KIND_REBASE_OBJECT_RESOLVABLE")
    assert hasattr(operation_evidence, "ownership_fingerprint")


def test_subprocess_collector_returns_unreadable_when_head_missing(tmp_path: Path) -> None:
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


def test_subprocess_collector_verify_treats_unreadable_reachability_as_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record()
    observed = (OperationFact(kind=_KIND_MERGE_OBJECT_RESOLVABLE, value="unreadable"),)
    collector = SubprocessOperationEvidenceCollector()
    assert collector.verify(repo, record, observed) is False


def test_in_memory_collector_evaluate_returns_verdict(tmp_path: Path) -> None:
    facts = (OperationFact(kind="head", value="abc"),)
    collector = InMemoryOperationEvidenceCollector(facts=facts)
    record = _record()

    assert collector.evaluate(tmp_path, record, facts) == EvidenceVerdict.UNCHANGED

    differing = (OperationFact(kind="head", value="def"),)
    assert collector.evaluate(tmp_path, record, differing) == EvidenceVerdict.CHANGED

    unreadable_obs = (OperationFact(kind="head", value="unreadable"),)
    assert collector.evaluate(tmp_path, record, unreadable_obs) == EvidenceVerdict.UNKNOWN

    collector_unreadable = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="head", value="unreadable"),)
    )
    assert collector_unreadable.evaluate(tmp_path, record, facts) == EvidenceVerdict.UNKNOWN

    collector_unreachable = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind=_KIND_MERGE_OBJECT_RESOLVABLE, value="False"),)
    )
    assert (
        collector_unreachable.evaluate(
            tmp_path,
            record,
            (OperationFact(kind=_KIND_MERGE_OBJECT_RESOLVABLE, value="False"),),
        )
        == EvidenceVerdict.CHANGED
    )


def test_subprocess_collector_evaluate_returns_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _mock_git(monkeypatch, tmp_path / "repo", head_sha="a" * 40)
    record = _record()
    collector = SubprocessOperationEvidenceCollector()
    live_facts = collector.snapshot(repo, record)

    assert collector.evaluate(repo, record, live_facts) == EvidenceVerdict.UNCHANGED

    differing = (OperationFact(kind="head", value="0" * 40),)
    assert collector.evaluate(repo, record, differing) == EvidenceVerdict.CHANGED

    assert collector.evaluate(repo, record, ()) == EvidenceVerdict.CHANGED

    unreadable_obs = (OperationFact(kind="head", value="unreadable"),)
    assert collector.evaluate(repo, record, unreadable_obs) == EvidenceVerdict.UNKNOWN


def test_subprocess_collector_guard_rejects_forbidden_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _mock_git(monkeypatch, tmp_path / "repo")
    record = _record()

    from ralph.pipeline import operation_evidence as op_ev_module

    def _poisoned_state_files(_root: Path) -> tuple[object, ...]:
        return (OperationFact(kind="for-each-ref", value="forbidden"),)

    monkeypatch.setattr(
        op_ev_module,
        "_read_rebase_state_files",
        _poisoned_state_files,
    )
    with pytest.raises(RuntimeError, match="forbidden kind"):
        SubprocessOperationEvidenceCollector().snapshot(repo, record)


def test_subprocess_collector_guard_accepts_only_closed_vocabulary() -> None:
    import ralph.pipeline.operation_evidence as op_ev

    closed = op_ev._CLOSED_OPERATION_FACT_KINDS
    assert isinstance(closed, frozenset)

    allowed = (
        "head",
        "worktree_path",
        "index_scope",
        "rebase_state_files",
        "merge_state_files",
        "ownership_fingerprint",
        "unstaged_work",
        "commit_controls",
        "merge_object_resolvable",
        "rebase_object_resolvable",
    )
    forbidden = ("for-each-ref", "ls-files", "logs/HEAD", "unknown_kind")
    for kind in allowed:
        assert kind in closed, kind
    for kind in forbidden:
        assert kind not in closed, kind
