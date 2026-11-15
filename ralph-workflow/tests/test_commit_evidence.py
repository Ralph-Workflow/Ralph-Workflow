from pathlib import Path

from ralph.prompts import commit_evidence


class _CommitEvidenceSource:
    def __init__(self, paths: tuple[list[str], ...], diff: str = "diff") -> None:
        self._paths = iter(paths)
        self._diff = diff

    def list_changed_paths(self, _repo_root: Path) -> list[str]:
        return next(self._paths)

    def commit_generation_diff(self, _repo_root: Path) -> str:
        return self._diff


def _source(paths: list[str], diff: str = "diff") -> _CommitEvidenceSource:
    return _CommitEvidenceSource((paths,), diff)


def test_build_commit_evidence_bundle_is_fresh(tmp_path: Path) -> None:
    source = _CommitEvidenceSource((["ralph/one.py"], ["ralph/one.py", "tests/test_one.py"]))

    first = commit_evidence.build_commit_evidence_bundle(tmp_path, source=source)
    second = commit_evidence.build_commit_evidence_bundle(tmp_path, source=source)

    assert first.changed_files == ("ralph/one.py",)
    assert second.changed_files == ("ralph/one.py", "tests/test_one.py")
    assert second.change_areas == ("ralph", "tests")


def test_evidence_bundle_captures_verification_compatibility_and_risk(
    tmp_path: Path,
) -> None:
    source = _source(["ralph/mcp/api.py", "ralph/pipeline/commit.py", "tests/test_api.py"])

    bundle = commit_evidence.build_commit_evidence_bundle(tmp_path, source=source)

    assert bundle.verification_hints
    assert bundle.compatibility_hints == ("review public API compatibility",)
    assert bundle.risk_hints == ("review commit staging and secret handling",)


def test_evidence_bundle_reads_persisted_behavior_and_verification_facts(
    tmp_path: Path,
) -> None:
    source = _source(["ralph/app.py"])
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "development_result.md").write_text(
        "---\ntype: development_result\nstatus: completed\n---\n\n"
        "## Summary\n\n- [SUM-1] Preserve retry status for callers.\n",
        encoding="utf-8",
    )
    (artifacts / "parallel_development_summary.md").write_text(
        "## Verification\n\nRan: yes — passed\n", encoding="utf-8"
    )

    bundle = commit_evidence.build_commit_evidence_bundle(tmp_path, source=source)

    assert bundle.behavior_facts == ("[SUM-1] Preserve retry status for callers.",)
    assert bundle.verification_facts == ("post-fanout workspace verification passed",)


def test_message_budget_elevates_compatibility_or_verification_over_equal_scope() -> None:
    base = commit_evidence.CommitEvidenceBundle("diff", ("docs/guide.md",), ("docs",), (), ())
    compatibility = commit_evidence.CommitEvidenceBundle(
        "diff", ("docs/guide.md",), ("docs",), (), (), compatibility_hints=("migration required",)
    )
    verified = commit_evidence.CommitEvidenceBundle(
        "diff", ("docs/guide.md",), ("docs",), (), (), verification_facts=("pytest passed",)
    )

    assert base.message_budget.tier == "small"
    assert compatibility.message_budget.tier == verified.message_budget.tier == "medium"


def test_evidence_bundle_does_not_turn_suggestions_into_verification_facts(
    tmp_path: Path,
) -> None:
    source = _source(["ralph/app.py"])

    bundle = commit_evidence.build_commit_evidence_bundle(tmp_path, source=source)

    assert bundle.verification_hints == ("run the verification gate",)
    assert bundle.verification_facts == ()
