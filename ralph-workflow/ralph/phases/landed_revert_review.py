from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_RECENT_MAINLINE_COMMITS: Final[int] = 20
_COMMIT_MARK: Final[str] = "\x00"
_MISSING: Final[str] = "missing"
_SHOWN_FINDINGS: Final[int] = 10
_REFLOG_DEPTH: Final[int] = 500
_MIN_UNDONE_PATHS: Final[int] = 2


def _own_commit_subjects(repo_root: Path) -> set[str]:
    result = run_git(
        ("reflog", "show", f"--max-count={_REFLOG_DEPTH}", "--format=%gs", "HEAD"),
        cwd=repo_root,
        label="commit-review:landed-revert-reflog",
    )
    subjects: set[str] = set()
    if result.returncode != 0:
        return subjects
    for line in result.stdout.splitlines():
        subjects.add(line)
        subjects.add(line.partition(": ")[2])
    subjects.discard("")
    return subjects


def _last_touching_commits(repo_root: Path, base: str) -> dict[str, tuple[str, str]]:
    result = run_git(
        (
            "log",
            "--first-parent",
            f"--max-count={_RECENT_MAINLINE_COMMITS}",
            "--no-renames",
            "--name-only",
            "--format=%x00%H%x00%s",
            base,
        ),
        cwd=repo_root,
        label="commit-review:landed-revert-log",
    )
    touched: dict[str, tuple[str, str]] = {}
    if result.returncode != 0:
        return touched
    commit = ("", "")
    for line in result.stdout.splitlines():
        if line.startswith(_COMMIT_MARK):
            sha, _sep, subject = line[1:].partition(_COMMIT_MARK)
            commit = (sha, subject)
        elif line and commit[0]:
            touched.setdefault(line, commit)
    return touched


def _blob_ids(repo_root: Path, specs: list[str]) -> list[str]:
    result = run_git(
        ("cat-file", "--batch-check=%(objectname)"),
        cwd=repo_root,
        label="commit-review:landed-revert-blobs",
        options=GitRunOptions(input_data="".join(f"{spec}\n" for spec in specs)),
    )
    lines = result.stdout.splitlines() if result.returncode == 0 else []
    if len(lines) != len(specs):
        return []
    return [_MISSING if line.endswith(f" {_MISSING}") else line for line in lines]


def proposal_reverted_landings(
    repo_root: Path, base: str, tree_sha: str, changed_paths: Sequence[str]
) -> tuple[str, ...]:
    recent = _last_touching_commits(repo_root, base)
    if not any(path in recent for path in changed_paths):
        return ()
    own = _own_commit_subjects(repo_root)
    touched = {path: commit for path, commit in recent.items() if commit[1] not in own}
    candidates = [path for path in changed_paths if path in touched]
    if not candidates:
        return ()
    specs: list[str] = []
    for path in candidates:
        specs.extend((f"{tree_sha}:{path}", f"{touched[path][0]}^:{path}"))
    blobs = _blob_ids(repo_root, specs)
    if not blobs:
        return ()
    undone: dict[str, list[str]] = {}
    modified: set[str] = set()
    for index, path in enumerate(candidates):
        proposed, before_landing = blobs[2 * index], blobs[2 * index + 1]
        if proposed != before_landing:
            continue
        sha = touched[path][0]
        undone.setdefault(sha, []).append(path)
        if before_landing != _MISSING:
            modified.add(sha)
    return tuple(
        f"{path} (undoes {sha[:9]} {touched[paths[0]][1]})"
        for sha, paths in undone.items()
        if len(paths) >= _MIN_UNDONE_PATHS and sha in modified
        for path in paths
    )


def render_landed_revert_reason(findings: tuple[str, ...]) -> str:
    shown = "; ".join(findings[:_SHOWN_FINDINGS])
    more = len(findings) - _SHOWN_FINDINGS
    suffix = f"; and {more} more" if more > 0 else ""
    return (
        f"The proposal undoes work already landed on the mainline ({len(findings)} path(s)): "
        f"{shown}{suffix}. Each listed path is back to its content from before that commit "
        "landed, which usually means a stale rollback or restore overwrote it. Restore each "
        "path to its committed content (keep the landed commit's change) and re-apply only "
        "this run's own edits on top of it."
    )


__all__ = ["proposal_reverted_landings", "render_landed_revert_reason"]
