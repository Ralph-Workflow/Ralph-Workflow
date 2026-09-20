from pathlib import Path

from ralph.prompts import commit_evidence


def test_build_commit_evidence_bundle_is_fresh(monkeypatch, tmp_path: Path) -> None:
    paths = iter((["ralph/one.py"], ["ralph/one.py", "tests/test_one.py"]))
    monkeypatch.setattr(commit_evidence, "list_changed_paths", lambda _root: next(paths))
    monkeypatch.setattr(commit_evidence, "commit_generation_diff", lambda _root: "diff")

    first = commit_evidence.build_commit_evidence_bundle(tmp_path)
    second = commit_evidence.build_commit_evidence_bundle(tmp_path)

    assert first.changed_files == ("ralph/one.py",)
    assert second.changed_files == ("ralph/one.py", "tests/test_one.py")
    assert second.change_areas == ("ralph", "tests")


def test_evidence_bundle_captures_verification_compatibility_and_risk(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        commit_evidence,
        "list_changed_paths",
        lambda _root: ["ralph/mcp/api.py", "ralph/pipeline/commit.py", "tests/test_api.py"],
    )
    monkeypatch.setattr(commit_evidence, "commit_generation_diff", lambda _root: "diff")

    bundle = commit_evidence.build_commit_evidence_bundle(tmp_path)

    assert bundle.verification_hints
    assert bundle.compatibility_hints == ("review public API compatibility",)
    assert bundle.risk_hints == ("review commit staging and secret handling",)


def test_evidence_bundle_reads_persisted_behavior_and_verification_facts(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(commit_evidence, "list_changed_paths", lambda _root: ["ralph/app.py"])
    monkeypatch.setattr(commit_evidence, "commit_generation_diff", lambda _root: "diff")
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

    bundle = commit_evidence.build_commit_evidence_bundle(tmp_path)

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
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(commit_evidence, "list_changed_paths", lambda _root: ["ralph/app.py"])
    monkeypatch.setattr(commit_evidence, "commit_generation_diff", lambda _root: "diff")

    bundle = commit_evidence.build_commit_evidence_bundle(tmp_path)

    assert bundle.verification_hints == ("run the verification gate",)
    assert bundle.verification_facts == ()
