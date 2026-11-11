from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ralph.config import bootstrap as bs_module
from ralph.config.bootstrap import (
    _DEFAULT_GITIGNORE_PATTERNS,
    auto_seed_default_gitignore,
)


def test_auto_seed_creates_gitignore_when_missing(tmp_path: Path) -> None:
    assert not (tmp_path / ".gitignore").exists()

    appended = auto_seed_default_gitignore(tmp_path)

    gitignore_path = tmp_path / ".gitignore"
    assert gitignore_path.exists(), ".gitignore must be created"
    content = gitignore_path.read_text(encoding="utf-8")
    for pattern in _DEFAULT_GITIGNORE_PATTERNS:
        assert pattern in content, f"Missing default pattern: {pattern!r}"
    assert appended == list(_DEFAULT_GITIGNORE_PATTERNS), (
        f"Expected all default patterns to be returned on a fresh repo; "
        f"got {len(appended)} patterns"
    )
    assert len(appended) == len(_DEFAULT_GITIGNORE_PATTERNS)


def test_auto_seed_is_idempotent(tmp_path: Path) -> None:
    gitignore_path = tmp_path / ".gitignore"
    gitignore_path.write_text("\n".join(_DEFAULT_GITIGNORE_PATTERNS) + "\n", encoding="utf-8")
    pre_sha = hashlib.sha256(gitignore_path.read_bytes()).hexdigest()

    appended = auto_seed_default_gitignore(tmp_path)

    assert appended == [], f"Expected empty list on idempotent call, got {appended!r}"
    post_sha = hashlib.sha256(gitignore_path.read_bytes()).hexdigest()
    assert pre_sha == post_sha, "File must be byte-identical after a no-op re-seed"


def test_auto_seed_preserves_user_patterns(tmp_path: Path) -> None:
    gitignore_path = tmp_path / ".gitignore"
    user_line = "# my custom ignore"
    custom_block = "\n".join((user_line, *_DEFAULT_GITIGNORE_PATTERNS))
    gitignore_path.write_text(custom_block + "\n", encoding="utf-8")

    appended = auto_seed_default_gitignore(tmp_path)

    assert appended == [], f"Expected empty list, got {appended!r}"
    content_lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    assert content_lines[0] == user_line, (
        f"User-customized first line must be preserved; got: {content_lines[:3]!r}"
    )
    for pattern in _DEFAULT_GITIGNORE_PATTERNS:
        assert pattern in content_lines, f"Missing default pattern: {pattern!r}"


def test_auto_seed_default_gitignore_uses_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[Path, str]] = []
    real_atomic = bs_module._atomic_append_text

    def spy_atomic(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
        captured.append((path, payload))
        return real_atomic(path, payload, encoding=encoding)

    monkeypatch.setattr(bs_module, "_atomic_append_text", spy_atomic)

    auto_seed_default_gitignore(tmp_path)

    assert captured, "auto_seed_default_gitignore must route through _atomic_append_text"
    gitignore_path = tmp_path / ".gitignore"
    assert any(p == gitignore_path for p, _ in captured), (
        f"Atomic helper must be called with the .gitignore path; "
        f"observed paths: {[p for p, _ in captured]!r}"
    )


def test_auto_seed_idempotent_repair_appends_child_rule_only_when_absent(tmp_path: Path) -> None:
    appended_first = auto_seed_default_gitignore(tmp_path)
    assert ".agent/ralph-explore/" in appended_first
    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".agent/ralph-explore/" in gitignore_text.splitlines()

    gitignore = tmp_path / ".gitignore"
    user_line = "# user custom line"
    existing = gitignore.read_text(encoding="utf-8")
    gitignore.write_text(user_line + "\n" + existing, encoding="utf-8")

    appended_second = auto_seed_default_gitignore(tmp_path)
    assert appended_second == []
    after = gitignore.read_text(encoding="utf-8").splitlines()
    assert after[0] == user_line
    assert ".agent/ralph-explore/" in after

    new_lines = [line for line in after if line.strip() != ".agent/ralph-explore/"]
    gitignore.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    appended_third = auto_seed_default_gitignore(tmp_path)
    assert ".agent/ralph-explore/" in appended_third
    final_text = gitignore.read_text(encoding="utf-8")
    assert ".agent/ralph-explore/" in final_text.splitlines()
    assert final_text.count(".agent/") >= 1


def test_auto_seed_covers_common_project_structures(tmp_path: Path) -> None:
    auto_seed_default_gitignore(tmp_path)
    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")

    assert "__pycache__/" in gitignore_text, f"Python category missing; got:\n{gitignore_text}"
    assert "node_modules/" in gitignore_text, f"Node category missing; got:\n{gitignore_text}"
    assert ".idea/" in gitignore_text, f"editor category missing; got:\n{gitignore_text}"
    assert ".DS_Store" in gitignore_text, f"OS category missing; got:\n{gitignore_text}"


def test_auto_seed_gitignore_warns_on_unreadable_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from loguru import logger

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# user line\n", encoding="utf-8")

    original_read_text = type(gitignore).read_text

    def _failing_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == gitignore:
            raise PermissionError(13, "Permission denied", str(self))
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _failing_read_text)

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        appended = auto_seed_default_gitignore(tmp_path)
    finally:
        logger.remove(sink_id)

    assert appended, (
        "best-effort seeder MUST still append defaults when the existing"
        " file cannot be read; got an empty appended list"
    )
    warning = "\n".join(records)
    assert str(gitignore) in warning, (
        f"OSError warning MUST name the unreadable .gitignore; got: {warning!r}"
    )
    assert "Could not read existing .gitignore" in warning, (
        f"OSError warning MUST label the failure; got: {warning!r}"
    )
    assert "non-fatal" in warning or "best-effort" in warning or "Proceeding" in warning, (
        f"OSError warning MUST mark the failure as non-fatal / best-effort; got: {warning!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_auto_seed_git_exclude_warns_on_unreadable_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from git import Repo
    from loguru import logger

    from ralph.config.bootstrap import auto_seed_default_git_exclude

    Repo.init(tmp_path)
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    assert exclude_path.exists(), (
        "Repo.init must materialise .git/info/exclude in this GitPython build"
    )
    exclude_path.write_text("# user line\n", encoding="utf-8")

    original_read_text = type(exclude_path).read_text

    def _failing_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == exclude_path:
            raise PermissionError(13, "Permission denied", str(self))
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _failing_read_text)

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        appended = auto_seed_default_git_exclude(tmp_path)
    finally:
        logger.remove(sink_id)

    assert appended, (
        "best-effort seeder MUST still append defaults when the existing"
        " exclude file cannot be read; got an empty appended list"
    )
    warning = "\n".join(records)
    assert str(exclude_path) in warning, (
        f"OSError warning MUST name the unreadable exclude file; got: {warning!r}"
    )
    assert "Could not read existing git exclude" in warning, (
        f"OSError warning MUST label the failure; got: {warning!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_gitignore_seed_is_committed_immediately(tmp_git_repo: Path) -> None:
    from git import Actor, Repo

    repo = Repo(tmp_git_repo)
    try:
        actor = Actor("Test Author", "test@example.com")
        (tmp_git_repo / "seed.txt").write_text("x\n", encoding="utf-8")
        repo.index.add(["seed.txt"])
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()

    appended = auto_seed_default_gitignore(tmp_git_repo)
    assert appended, "setup invariant: a fresh repo must append default patterns"

    repo = Repo(tmp_git_repo)
    try:
        assert not repo.is_dirty(untracked_files=True), (
            "the gitignore seed MUST be committed before returning; "
            f"dirty paths: {repo.git.status('--porcelain')!r}"
        )
        committed_files = set(repo.head.commit.stats.files)
        assert ".gitignore" in committed_files
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_ensure_default_gitignore_commits_in_git_workspace(tmp_git_repo: Path) -> None:
    from git import Actor, Repo

    repo = Repo(tmp_git_repo)
    try:
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()

    auto_seed_default_gitignore(tmp_git_repo)

    repo = Repo(tmp_git_repo)
    try:
        assert not repo.is_dirty(untracked_files=True), (
            f"bootstrap gitignore append MUST be committed; got: {repo.git.status('--porcelain')!r}"
        )
    finally:
        repo.close()
