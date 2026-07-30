"""Index-authoritative detection for markerless Git conflicts.

Most conflict shapes differ only in the unmerged index code Git reports. The
closed-set parser contract is therefore parameterized in memory. One real-Git
modify/delete test remains because proving that Git emits an unmerged entry
without writing conflict markers is an external integration contract; repeating
the same repository construction for binary, mode, symlink, and gitlink shapes
did not exercise a different Ralph code path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.git.hardening import parse_porcelain_z, unmerged_paths_z
from ralph.git.rebase import get_conflicted_files


def test_every_unmerged_index_code_is_detected_without_marker_scan() -> None:
    """The index code alone identifies markerless and textual conflicts.

    All codes enter the same pure parser branch, so a compact table avoids
    seven separately scheduled test items while retaining the closed-set pin.
    """
    cases = [
        ("UU", "both modified / binary / mode / symlink / gitlink"),
        ("AA", "both added"),
        ("DD", "both deleted"),
        ("AU", "added by us"),
        ("UA", "added by them"),
        ("DU", "deleted by us"),
        ("UD", "deleted by them"),
    ]

    for code, conflict_shape in cases:
        path = f"{conflict_shape.replace(' ', '-')}.dat"
        entries = unmerged_paths_z(f"{code}{path}\0")

        assert [(entry.xy, entry.path) for entry in entries] == [(code, path)]


def test_non_conflict_statuses_are_not_reported_as_unmerged() -> None:
    """Ordinary modifications and untracked files do not trigger resolution."""
    assert unmerged_paths_z(" Mmodified.txt\0??untracked.txt\0") == []


def test_rename_porcelain_preserves_source_and_live_destination() -> None:
    """A NUL-delimited rename exposes both paths without line parsing."""
    entries = parse_porcelain_z("R old/name.txt\0new/name.txt\0")

    assert len(entries) == 1
    assert entries[0].rename_source == "old/name.txt"
    assert entries[0].path == "new/name.txt"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _commit(repo: Path, path: str, content: str, message: str) -> None:
    (repo / path).write_text(content, encoding="utf-8")
    assert _git(repo, "add", path).returncode == 0
    assert _git(repo, "commit", "-m", message).returncode == 0


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_real_modify_delete_conflict_is_found_from_index_without_markers(
    tmp_git_repo: Path,
) -> None:
    """Git's markerless modify/delete shape reaches the shared detector."""
    base = _git(tmp_git_repo, "branch", "--show-current").stdout.strip()
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed shared")
    assert _git(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    _commit(tmp_git_repo, "shared.txt", "feature\n", "feature modifies")
    assert _git(tmp_git_repo, "checkout", base).returncode == 0
    assert _git(tmp_git_repo, "rm", "shared.txt").returncode == 0
    assert _git(tmp_git_repo, "commit", "-m", "target deletes").returncode == 0
    assert _git(tmp_git_repo, "checkout", "feature").returncode == 0

    merge = _git(tmp_git_repo, "merge", "--no-commit", "--no-ff", base)

    assert merge.returncode != 0
    assert get_conflicted_files(repo_root=tmp_git_repo) == ["shared.txt"]
    content = (tmp_git_repo / "shared.txt").read_text(encoding="utf-8")
    assert content == "feature\n"
    assert "<<<<<<<" not in content
