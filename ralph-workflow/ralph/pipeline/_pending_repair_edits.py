"""Reverify explicitly declared source corrections without absorbing existing work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ralph.git.merge import staged_conflict_marker_paths, unmerged_paths
from ralph.git.subprocess_runner import run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline._pending_merge_commit import _git_value, pending_merge_identity_matches
from ralph.pipeline._pending_rebase_continue import pending_rebase_identity_matches
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend


def capture_unstaged_work(root: Path) -> dict[str, str]:
    result = run_git(("diff", "--name-only", "-z"), cwd=root, label="repair:dirty-paths")
    if result.returncode:
        raise RuntimeError("cannot enumerate existing unstaged work")
    captured: dict[str, str] = {}
    for path in filter(None, result.stdout.split("\0")):
        diff = run_git(
            (
                "--literal-pathspecs",
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                path,
            ),
            cwd=root,
            label="repair:dirty-snapshot",
        )
        if diff.returncode:
            raise RuntimeError(f"cannot protect existing work in {path}")
        captured[path] = diff.stdout
    loose = run_git(
        ("ls-files", "--others", "--exclude-standard", "-z"),
        cwd=root,
        label="repair:untracked",
    )
    if loose.returncode:
        raise RuntimeError("cannot enumerate pre-existing untracked work")
    for path in filter(None, loose.stdout.split("\0")):
        if path.startswith(".agent/"):
            continue
        digest = run_git(
            ("hash-object", "--no-filters", "--", path),
            cwd=root,
            label="repair:untracked-snapshot",
        )
        if digest.returncode:
            raise RuntimeError(f"cannot protect untracked work in {path}")
        captured[path] = f"untracked:{digest.stdout.strip()}"
    return captured


def accept_repair_edits(
    root: Path,
    record: IntegrationRecord,
    before: dict[str, str],
    *,
    backend: FileBackend,
) -> bool:
    """Admit declared edits only after proving existing work and Git parents unchanged."""
    matches = (
        pending_rebase_identity_matches(root, record)
        if record.rebase_continue_pending
        else pending_merge_identity_matches(root, record)
    )
    if not matches:
        raise RuntimeError("repair changed the prepared index or operation identity")
    after = capture_unstaged_work(root)
    if any(after.get(path) != content for path, content in before.items()):
        raise RuntimeError("repair touched pre-existing unstaged work; no source edits staged")
    changed = set(after) - set(before)
    if not changed:
        return True
    manifest = root / ".agent" / "tmp" / "pending_commit_repair_paths.json"
    value: object = json.loads(backend.read_text(manifest))
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        raise RuntimeError("repair must declare its source paths as a JSON string array")
    declared = {path for path in value if isinstance(path, str)}
    if declared != changed or any(
        PurePosixPath(path).is_absolute()
        or ".." in PurePosixPath(path).parts
        or path.startswith((".git/", ".agent/", ".githooks/"))
        for path in declared
    ):
        raise RuntimeError(
            "repair source edits differ from declared scope or include protected paths"
        )
    original = (
        record.rebase_continue_tree if record.rebase_continue_pending else record.merge_commit_tree
    )
    if original is None:
        raise RuntimeError("source correction baseline missing")
    patch = _proposed_patch(root, original, tuple(sorted(declared)))
    current = read_record(root)
    if current is None:
        raise RuntimeError("repair ownership unreadable before staging")
    staged_transition = current.model_copy(
        update={
            "repair_pending_diff": patch,
            "repair_original_tree": record.repair_original_tree or original,
            "repair_pending_paths": tuple(sorted(declared)),
        }
    )
    write_record(root, staged_transition, backend=backend)
    result = run_git(
        ("--literal-pathspecs", "add", "--", *sorted(declared)),
        cwd=root,
        label="repair:stage-scoped",
    )
    if result.returncode or unmerged_paths(root) or staged_conflict_marker_paths(root):
        raise RuntimeError("repair source corrections failed staging or conflict verification")
    resumed = resume_source_repair(root, staged_transition, backend=backend)
    if isinstance(resumed, str):
        raise RuntimeError(resumed)
    return True


def resume_source_repair(
    root: Path,
    record: IntegrationRecord,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> IntegrationRecord | str:
    field = "rebase_continue_tree" if record.rebase_continue_pending else "merge_commit_tree"
    baseline = (
        record.rebase_continue_tree if record.rebase_continue_pending else record.merge_commit_tree
    )
    if not baseline:
        return "source repair baseline unavailable; integration retained"
    patch = run_git(
        (
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            baseline,
        ),
        cwd=root,
        label="repair:staged-proof",
    )
    if patch.returncode:
        return "source correction index unreadable; integration retained"
    if patch.stdout != record.repair_pending_diff and record.repair_pending_paths:
        if not _stage_after_crash(root, record, baseline):
            return "prepared source correction changed or could not be staged; integration retained"
        patch = run_git(
            (
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                baseline,
            ),
            cwd=root,
            label="repair:restaged-proof",
        )
    if patch.returncode or patch.stdout != record.repair_pending_diff:
        return "source correction staging incomplete or changed; integration retained"
    tree = _git_value(root, "write-tree")
    revised = record.model_copy(
        update={
            field: tree,
            "repair_pending_diff": None,
            "repair_pending_paths": (),
        }
    )
    verified = tree is not None and (
        pending_rebase_identity_matches(root, revised)
        if revised.rebase_continue_pending
        else pending_merge_identity_matches(root, revised)
    )
    if not verified:
        return "repair operation changed during scoped staging; landing withheld"
    write_record(root, revised, backend=backend)
    return revised


def _stage_after_crash(root: Path, record: IntegrationRecord, baseline: str) -> bool:
    proposed = _proposed_patch(root, baseline, record.repair_pending_paths)
    staged_paths = run_git(
        ("diff", "--cached", "--name-only", "-z", baseline),
        cwd=root,
        label="repair:partial-index-paths",
    )
    tree = _git_value(root, "write-tree")
    field = "rebase_continue_tree" if record.rebase_continue_pending else "merge_commit_tree"
    staged_record = record.model_copy(update={field: tree})
    matches = tree is not None and (
        pending_rebase_identity_matches(root, staged_record)
        if record.rebase_continue_pending
        else pending_merge_identity_matches(root, staged_record)
    )
    if (
        proposed != record.repair_pending_diff
        or not matches
        or staged_paths.returncode
        or not set(filter(None, staged_paths.stdout.split("\0"))).issubset(
            record.repair_pending_paths
        )
    ):
        return False
    for path in filter(None, staged_paths.stdout.split("\0")):
        staged_delta = run_git(
            (
                "--literal-pathspecs",
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                baseline,
                "--",
                path,
            ),
            cwd=root,
            label="repair:partial-index-proof",
        )
        if staged_delta.returncode or staged_delta.stdout != _proposed_patch(
            root, baseline, (path,)
        ):
            return False
    return (
        run_git(
            ("--literal-pathspecs", "add", "--", *record.repair_pending_paths),
            cwd=root,
            label="repair:resume-stage",
        ).returncode
        == 0
    )


def _proposed_patch(root: Path, baseline: str, paths: tuple[str, ...]) -> str:
    patches: list[str] = []
    for path in paths:
        exists = run_git(
            ("cat-file", "-e", f"{baseline}:{path}"), cwd=root, label="repair:baseline-path"
        )
        options = ("--binary", "--full-index", "--no-renames", "--no-ext-diff", "--no-textconv")
        args = (
            ("--literal-pathspecs", "diff", *options, baseline, "--", path)
            if exists.returncode == 0
            else ("--literal-pathspecs", "diff", "--no-index", *options, "--", os.devnull, path)
        )
        result = run_git(args, cwd=root, label="repair:proposed-path")
        if result.returncode not in ({0} if exists.returncode == 0 else {0, 1}):
            raise RuntimeError(f"cannot preserve proposed correction in {path}")
        patches.append(result.stdout)
    return "".join(patches)


def capture_commit_controls(root: Path, backend: FileBackend = DEFAULT_FILE_BACKEND) -> str:
    config = run_git(
        ("config", "--get-regexp", "^(core\\.hookspath|commit\\.gpgsign|gpg\\.program)$"),
        cwd=root,
        label="repair:commit-controls",
    )
    hooks = _git_value(root, "rev-parse", "--git-path", "hooks")
    if config.returncode not in {0, 1} or hooks is None:
        raise RuntimeError("cannot protect commit controls")
    directory = root / hooks
    scripts = tuple(
        (str(path), backend.read_bytes(path), os.access(path, os.X_OK))
        for path in sorted(backend.glob(directory, "*"))
        if not path.name.endswith(".sample")
    )
    return hashlib.sha256(repr((config.stdout, scripts)).encode()).hexdigest()
