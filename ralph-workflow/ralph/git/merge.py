
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.errors import GitOperationError
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge_obstructions import clear_untracked_merge_obstructions, merge_refusal
from ralph.git.operations import _read_ref_from_disk, _resolve_gitdir_paths
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_CONFLICT_MARKER_PREFIXES: tuple[str, ...] = ("<<<<<<< ", "=======", ">>>>>>> ")

_UNRESOLVED_CONFLICT_FENCES: frozenset[str] = frozenset({"<<<<<<< ", ">>>>>>> "})

DEFAULT_CONFLICT_MARKER_SIZE = 7

_MIN_GUESSED_FENCE = DEFAULT_CONFLICT_MARKER_SIZE


def _fence_pattern(size: int) -> re.Pattern[str]:
    width = max(1, size)
    return re.compile(rf"^(?:<{{{width},}}|>{{{width},}})(?:[ \t]|$)")


_UNRESOLVED_FENCE_PATTERN = _fence_pattern(DEFAULT_CONFLICT_MARKER_SIZE)


def staged_conflict_marker_paths(repo_root: Path | str) -> list[str]:
    root = Path(repo_root)
    result = run_git(
        ("diff", "--cached", "--check"),
        cwd=root,
        label="git-staged-marker-check",
    )
    if result.returncode not in (0, 2):
        return ["<staged-marker-query-failed>"]
    reports: dict[str, list[int]] = {}
    for line in result.stdout.splitlines():
        head, marker, _rest = line.partition(": leftover conflict marker")
        if not marker:
            continue
        path, _, line_no = head.rpartition(":")
        if path:
            reports.setdefault(path, []).append(int(line_no) if line_no.isdigit() else 0)
    return [
        path
        for path, line_numbers in reports.items()
        if _staged_fence_reported(root, path, line_numbers)
    ]


def _staged_fence_reported(repo_root: Path, path: str, line_numbers: Sequence[int]) -> bool:
    if path.startswith('"'):
        return True
    try:
        blob = run_git(
            ("cat-file", "blob", f":0:{path}"),
            cwd=repo_root,
            label="git-staged-marker-line",
        )
    except (UnicodeDecodeError, ValueError):
        return True
    if blob.returncode != 0:
        return True
    lines = blob.stdout.split("\n")
    for line_no in line_numbers:
        if not 1 <= line_no <= len(lines):
            return True
        if lines[line_no - 1].startswith(("<", ">")):
            return True
    return False


def _readable_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for bom, encoding in (
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
    ):
        if data.startswith(bom):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                return None
    if b"\x00" in data:
        return data.replace(b"\x00", b"").decode("utf-8", errors="replace")
    return data.decode("utf-8", errors="replace")


def conflict_marker_sizes(repo_root: Path | str, paths: Sequence[str]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    if not paths:
        return sizes
    try:
        result = run_git(
            ("check-attr", "-z", "conflict-marker-size", "--", *paths),
            cwd=Path(repo_root),
            label="git-conflict-marker-size",
        )
        if result.returncode != 0:
            return sizes
        fields = [field for field in result.stdout.split("\0") if field != ""]
        for index in range(0, len(fields) - 2, 3):
            path, _attribute, value = fields[index], fields[index + 1], fields[index + 2]
            if value.isdigit():
                sizes[path] = int(value)
    except Exception as exc:
        logger.debug("git: could not read conflict-marker-size: {}", exc)
    return sizes


MERGE_STATE_NONE = "none"
MERGE_STATE_IN_PROGRESS = "in_progress"
MERGE_STATE_UNKNOWN = "unknown"

WORKTREE_FOUND = "found"
WORKTREE_NOT_CHECKED_OUT = "not_checked_out"
WORKTREE_QUERY_FAILED = "query_failed"

UNMERGED_PATH_QUERY_FAILED = "<unmerged-path-query-failed>"


@dataclass(frozen=True)
class UnmergedPathsObservation:

    paths: tuple[str, ...] = ()
    query_ok: bool = True


@dataclass(frozen=True)
class MergeResult:

    outcome: str
    reason: str | None = None
    blocked_paths: tuple[str, ...] = ()
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE


def merge_refusal_obstruction(result: MergeResult) -> tuple[str, ...] | None:
    if (
        result.outcome == "conflict"
        and result.blocked_paths
        and result.failure_cause is ResolutionFailureCause.CONTEXT_FAILURE
    ):
        return result.blocked_paths
    return None


def branch_exists(repo_root: Path | str, name: str) -> bool:
    repo_root_path = Path(repo_root)
    result = run_git(
        ("show-ref", "--verify", "--quiet", f"refs/heads/{name}"),
        cwd=repo_root_path,
        label="git-branch-exists",
    )
    return result.returncode == 0


_REV_PARSE_REF_ABSENT_RETURNCODE = 1


def observe_branch_sha(repo_root: Path | str, name: str) -> tuple[str | None, bool]:
    repo_root_path = Path(repo_root)
    ref_spec = name if name.startswith("refs/") else f"refs/heads/{name}"
    try:
        gitdir, commondir = _resolve_gitdir_paths(repo_root_path)
        if gitdir is not None:
            search_dirs = [gitdir]
            if commondir is not None and commondir != gitdir:
                search_dirs.append(commondir)
            direct_sha = _read_ref_from_disk(ref_spec, search_dirs)
            if direct_sha is not None:
                return direct_sha, True
            if ref_spec.startswith("refs/heads/"):
                return None, True
    except (OSError, ValueError) as exc:
        logger.debug("direct branch sha lookup failed: {}", exc)
    result = run_git(
        ("rev-parse", "--verify", "--quiet", ref_spec),
        cwd=repo_root_path,
        label="git-branch-sha",
    )
    if result.returncode == 0:
        return result.stdout.strip() or None, True
    return None, result.returncode == _REV_PARSE_REF_ABSENT_RETURNCODE


def branch_sha(repo_root: Path | str, name: str) -> str | None:
    sha, _query_ok = observe_branch_sha(repo_root, name)
    return sha


def is_ancestor(repo_root: Path | str, ancestor: str, descendant: str) -> bool:
    if ancestor and ancestor == descendant:
        return True
    repo_root_path = Path(repo_root)
    result = run_git(
        ("merge-base", "--is-ancestor", ancestor, descendant),
        cwd=repo_root_path,
        label="git-is-ancestor",
    )
    return result.returncode == 0


def merge_state(repo_root: Path | str) -> str:
    repo_root_path = Path(repo_root)
    try:
        gitdir, _commondir = _resolve_gitdir_paths(repo_root_path)
        if gitdir is not None and gitdir.is_dir():
            if (gitdir / "MERGE_HEAD").exists():
                return MERGE_STATE_IN_PROGRESS
            return MERGE_STATE_NONE
    except (OSError, ValueError) as exc:
        logger.debug("direct merge state lookup failed: {}", exc)
    result = run_git(
        ("rev-parse", "--git-dir"),
        cwd=repo_root_path,
        label="git-merge-progress:git-dir",
    )
    if result.returncode != 0:
        return MERGE_STATE_UNKNOWN
    git_dir_raw = result.stdout.strip()
    git_dir = Path(git_dir_raw)
    if not git_dir.is_absolute():
        git_dir = (repo_root_path / git_dir).resolve()
    if (git_dir / "MERGE_HEAD").exists():
        return MERGE_STATE_IN_PROGRESS
    return MERGE_STATE_NONE


def merge_in_progress(repo_root: Path | str) -> bool:
    return merge_state(repo_root) == MERGE_STATE_IN_PROGRESS


def merge_target_into_current(
    repo_root: Path | str, target: str, *, keep_conflicts: bool = False
) -> MergeResult:
    repo_root_path = Path(repo_root)
    clear_untracked_merge_obstructions(repo_root_path, target)
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "merge", "--no-edit", "--", target),
        cwd=repo_root_path,
        label="git-merge",
    )
    if result.returncode == 0:
        return MergeResult(outcome="success", failure_cause=ResolutionFailureCause.NONE)
    refusal = merge_refusal(f"{result.stdout}\n{result.stderr}")
    if not keep_conflicts and not abort_merge(repo_root_path):
        state = merge_state(repo_root_path)
        if state != MERGE_STATE_NONE:
            logger.warning(
                "conflicted merge not proven aborted in {} (merge state"
                " {}); later integrations will be blocked until it is"
                " resolved",
                repo_root_path,
                state,
            )
    if refusal is not None:
        reason, blocked = refusal
        return MergeResult(
            outcome="conflict",
            reason=reason,
            blocked_paths=blocked,
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )
    return MergeResult(outcome="conflict", failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT)


def unmerged_paths(repo_root: Path | str) -> list[str]:
    observed = observe_unmerged_paths(repo_root)
    if not observed.query_ok:
        return [UNMERGED_PATH_QUERY_FAILED]
    return list(observed.paths)


def observe_unmerged_paths(repo_root: Path | str) -> UnmergedPathsObservation:
    repo_root_path = Path(repo_root)
    try:
        result = run_git(
            ("diff", "--name-only", "--diff-filter=U", "-z"),
            cwd=repo_root_path,
            label="git-unmerged-paths",
        )
    except Exception as exc:
        logger.debug("git: unmerged-path query failed in {}: {}", repo_root_path, exc)
        return UnmergedPathsObservation(query_ok=False)
    if result.returncode != 0:
        return UnmergedPathsObservation(query_ok=False)
    return UnmergedPathsObservation(
        tuple(entry for entry in result.stdout.split("\0") if entry),
        query_ok=True,
    )


def conflict_stage_entries(
    repo_root: Path | str, paths: Sequence[str]
) -> dict[str, dict[int, tuple[str, str]]]:
    if not paths:
        return {}
    result = run_git(
        ("ls-files", "-u", "-z", "--", *paths),
        cwd=Path(repo_root),
        label="git-conflict-stage-entries",
    )
    if result.returncode != 0:
        return {}

    entries: dict[str, dict[int, tuple[str, str]]] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        try:
            metadata, path = record.split("\t", 1)
            mode, blob, raw_stage = metadata.split()
            entries.setdefault(path, {})[int(raw_stage)] = (mode, blob)
        except ValueError:
            return {}
    return entries


def stage_paths(repo_root: Path | str, paths: Sequence[str]) -> bool:
    if not paths:
        return True
    result = run_git(
        ("add", "--", *paths),
        cwd=Path(repo_root),
        label="git-add-resolved",
    )
    return result.returncode == 0


def paths_with_conflict_markers(repo_root: Path | str, paths: Sequence[str]) -> list[str]:
    repo_root_path = Path(repo_root)
    sizes = conflict_marker_sizes(repo_root_path, list(paths))
    reported: list[str] = []
    for path in paths:
        content = _readable_text(repo_root_path / path)
        if content is None:
            if (repo_root_path / path).exists():
                reported.append(path)
            continue
        pattern = (
            _fence_pattern(sizes[path])
            if path in sizes and sizes[path] < _MIN_GUESSED_FENCE
            else _UNRESOLVED_FENCE_PATTERN
        )
        if any(pattern.match(line) for line in content.splitlines()):
            reported.append(path)
    return reported


def commit_merge_in_progress(
    repo_root: Path | str, *, on_failure: Callable[[str], None] | None = None
) -> bool:
    repo_root_path = Path(repo_root)
    if merge_state(repo_root_path) != MERGE_STATE_IN_PROGRESS:
        return False
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "commit", "--no-edit"),
        cwd=repo_root_path,
        label="git-merge-commit",
    )
    committed = result.returncode == 0 and merge_state(repo_root_path) == MERGE_STATE_NONE
    if not committed:
        output = f"{result.stdout}\n{result.stderr}".strip()
        reason = f"git commit --no-edit failed (exit {result.returncode}): {output[:2000] or 'merge completion unproven'}"
        logger.critical("{}", reason)
        if on_failure is not None:
            on_failure(reason)
    return committed


def abort_merge(repo_root: Path | str) -> bool:
    repo_root_path = Path(repo_root)
    state = merge_state(repo_root_path)
    if state == MERGE_STATE_NONE:
        return False
    result = run_git(
        ("merge", "--abort"),
        cwd=repo_root_path,
        label="git-merge-abort",
    )
    if result.returncode != 0:
        logger.warning(
            "git merge --abort failed in {} (merge state {}): {}",
            repo_root_path,
            state,
            result.stderr.strip() or result.stdout.strip() or "unknown error",
        )
        return False
    return True


def reset_keep(repo_root: Path | str, sha: str) -> None:
    result = run_git(("reset", "--keep", sha), cwd=Path(repo_root), label="git-reset-keep")
    if result.returncode != 0:
        raise GitOperationError("reset_keep", (result.stderr or result.stdout).strip())


def fast_forward_via_worktree(worktree_root: Path | str, feature_sha: str) -> bool:
    worktree_root_path = Path(worktree_root)
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "merge", "--ff-only", feature_sha),
        cwd=worktree_root_path,
        label="git-merge-ff-only",
        call_class="mutation",
    )
    return result.returncode == 0


def compare_and_swap_branch(
    repo_root: Path | str,
    target: str,
    expected_old_sha: str,
    new_sha: str,
) -> bool:
    repo_root_path = Path(repo_root)
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "update-ref", f"refs/heads/{target}", new_sha, expected_old_sha),
        cwd=repo_root_path,
        label="git-update-ref-cas",
        call_class="mutation",
    )
    return result.returncode == 0


def worktree_lookup(repo_root: Path | str, branch: str) -> tuple[str, Path | None]:
    repo_root_path = Path(repo_root)
    result = run_git(
        ("worktree", "list", "--porcelain"),
        cwd=repo_root_path,
        label="git-worktree-list",
    )
    if result.returncode != 0:
        logger.warning(
            "git worktree list failed in {}: {}",
            repo_root_path,
            result.stderr.strip() or result.stdout.strip() or "unknown error",
        )
        return WORKTREE_QUERY_FAILED, None
    target_ref = f"refs/heads/{branch}"
    current_path: Path | None = None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            current_path = None
            continue
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
        elif (
            line.startswith("branch ")
            and line == f"branch {target_ref}"
            and current_path is not None
        ):
            return WORKTREE_FOUND, current_path
    return WORKTREE_NOT_CHECKED_OUT, None


def worktree_for_branch(repo_root: Path | str, branch: str) -> Path | None:
    _verdict, path = worktree_lookup(repo_root, branch)
    return path


__all__ = [
    "MERGE_STATE_IN_PROGRESS",
    "MERGE_STATE_NONE",
    "MERGE_STATE_UNKNOWN",
    "UNMERGED_PATH_QUERY_FAILED",
    "WORKTREE_FOUND",
    "WORKTREE_NOT_CHECKED_OUT",
    "WORKTREE_QUERY_FAILED",
    "MergeResult",
    "UnmergedPathsObservation",
    "abort_merge",
    "branch_exists",
    "branch_sha",
    "compare_and_swap_branch",
    "conflict_stage_entries",
    "fast_forward_via_worktree",
    "is_ancestor",
    "merge_in_progress",
    "merge_refusal_obstruction",
    "merge_state",
    "merge_target_into_current",
    "observe_unmerged_paths",
    "paths_with_conflict_markers",
    "reset_keep",
    "stage_paths",
    "worktree_for_branch",
    "worktree_lookup",
]



