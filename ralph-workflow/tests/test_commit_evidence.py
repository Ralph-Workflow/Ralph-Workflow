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
