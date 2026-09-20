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
