from __future__ import annotations

import hashlib
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from ralph.git.subprocess_runner import run_git

__all__ = [
    "IntegrationPreservationBundle",
    "carry_preserved_commits",
    "preserve_integration_state",
    "reapply_preserved_progress",
]


@dataclass(frozen=True, slots=True)
class IntegrationPreservationBundle:
    manifest_path: str
    backup_refs: tuple[str, ...]
    worktree_archive: str
    index_copy: str | None
    work_diff: str
    resolution_copy: str | None
    carried_commits: tuple[str, ...]
    carry_failures: tuple[str, ...]
    intent_record: str


def _lines(root: Path, args: tuple[str, ...], label: str) -> tuple[str, ...]:
    result = run_git(args, cwd=root, label=label)
    if result.returncode != 0:
        return ()
    return tuple(line for line in result.stdout.splitlines() if line)


def _worktree_content(root: Path) -> tuple[str, ...]:
    listed = run_git(
        ("ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        cwd=root,
        label="integration-preservation:list-content",
    )
    if listed.returncode != 0:
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for name in str(listed.stdout).split("\0"):
        if not name or name in seen or name in {".git", ".agent"}:
            continue
        if name.startswith((".git/", ".agent/")):
            continue
        seen.add(name)
        names.append(name)
    return tuple(names)


def preserve_integration_state(
    root: Path,
    *,
    state_dir: Path,
    target: str,
) -> IntegrationPreservationBundle | None:
    token_source = f"{root.resolve()}:{time.time_ns()}".encode()
    token = hashlib.sha256(token_source).hexdigest()[:16]
    bundle_dir = state_dir / "integration-preservation" / token
    try:
        bundle_dir.mkdir(parents=True, exist_ok=False)
        tips = _lines(
            root,
            ("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/"),
            "integration-preservation:list-tips",
        )
        backup_refs: list[str] = []
        for row in tips:
            ref, separator, sha = row.partition(" ")
            if not separator or not sha:
                continue
            name = ref.removeprefix("refs/heads/").replace("/", "-")
            backup = f"refs/ralph-preserved/{token}/tips/{name}"
            created = run_git(
                ("update-ref", backup, sha),
                cwd=root,
                label="integration-preservation:backup-tip",
            )
            if created.returncode == 0:
                backup_refs.append(backup)
        for name in ("HEAD", "ORIG_HEAD", "REBASE_HEAD", "MERGE_HEAD"):
            tip = run_git(
                ("rev-parse", "--verify", name),
                cwd=root,
                label="integration-preservation:read-operation-tip",
            )
            sha = tip.stdout.strip()
            if tip.returncode != 0 or not sha:
                continue
            backup = f"refs/ralph-preserved/{token}/tips/{name.lower()}"
            created = run_git(
                ("update-ref", backup, sha),
                cwd=root,
                label="integration-preservation:backup-operation-tip",
            )
            if created.returncode == 0 and backup not in backup_refs:
                backup_refs.append(backup)

        archive_path = bundle_dir / "worktree.tar"
        with tarfile.open(archive_path, "w") as archive:
            for relative in _worktree_content(root):
                path = root / relative
                if path.is_file() or path.is_symlink():
                    archive.add(path, arcname=relative, recursive=False)

        index_path = bundle_dir / "index"
        index_source = _lines(
            root,
            ("rev-parse", "--path-format=absolute", "--git-path", "index"),
            "integration-preservation:index-path",
        )
        copied_index: str | None = None
        if index_source and Path(index_source[0]).is_file():
            shutil.copy2(index_source[0], index_path)
            copied_index = str(index_path)

        diff_path = bundle_dir / "work.diff"
        diff = run_git(
            ("diff", "--binary", "HEAD"),
            cwd=root,
            label="integration-preservation:work-diff",
        )
        diff_path.write_text(diff.stdout, encoding="utf-8")

        git_dir_rows = _lines(
            root,
            ("rev-parse", "--absolute-git-dir"),
            "integration-preservation:git-dir",
        )
        resolution_copy: str | None = None
        if git_dir_rows:
            rr_cache = Path(git_dir_rows[0]) / "rr-cache"
            if rr_cache.is_dir():
                resolution_dir = bundle_dir / "recorded-resolutions"
                shutil.copytree(rr_cache, resolution_dir)
                resolution_copy = str(resolution_dir)

        commits = _lines(
            root,
            ("rev-list", "--reverse", f"{target}..HEAD"),
            "integration-preservation:carryable-commits",
        )
        intent_path = bundle_dir / "intent.txt"
        intent = run_git(
            ("log", "--format=%H%n%B%n---", f"{target}..HEAD"),
            cwd=root,
            label="integration-preservation:intent",
        )
        intent_path.write_text(intent.stdout, encoding="utf-8")
        carry_failures = tuple(
            ("commit range could not be read",) if not commits and intent.returncode != 0 else ()
        )
        manifest_path = bundle_dir / "manifest.json"
        bundle = IntegrationPreservationBundle(
            manifest_path=str(manifest_path),
            backup_refs=tuple(backup_refs),
            worktree_archive=str(archive_path),
            index_copy=copied_index,
            work_diff=str(diff_path),
            resolution_copy=resolution_copy,
            carried_commits=commits,
            carry_failures=carry_failures,
            intent_record=str(intent_path),
        )
        manifest_path.write_text(
            "\n".join(
                (
                    f"worktree_archive={bundle.worktree_archive}",
                    f"index_copy={bundle.index_copy or ''}",
                    f"work_diff={bundle.work_diff}",
                    f"resolution_copy={bundle.resolution_copy or ''}",
                    f"intent_record={bundle.intent_record}",
                    *(f"backup_ref={ref}" for ref in bundle.backup_refs),
                    *(f"carryable_commit={commit}" for commit in bundle.carried_commits),
                    *(f"not_carried={failure}" for failure in bundle.carry_failures),
                )
            )
            + "\n",
            encoding="utf-8",
        )
        return bundle
    except OSError:
        return None


def carry_preserved_commits(
    root: Path,
    bundle: IntegrationPreservationBundle,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    carried: list[str] = []
    failures: list[str] = list(bundle.carry_failures)
    for commit in bundle.carried_commits:
        result = run_git(
            ("-c", "core.hooksPath=/dev/null", "cherry-pick", commit),
            cwd=root,
            label="integration-preservation:carry-commit",
        )
        if result.returncode == 0:
            carried.append(commit)
            continue
        run_git(
            ("-c", "core.hooksPath=/dev/null", "cherry-pick", "--abort"),
            cwd=root,
            label="integration-preservation:abort-carry",
        )
        failures.append(f"{commit}: did not replay cleanly")
    return tuple(carried), tuple(failures)


def reapply_preserved_progress(
    root: Path,
    bundle: IntegrationPreservationBundle,
) -> bool:
    restored_any = False
    if bundle.index_copy is not None:
        index_source = Path(bundle.index_copy)
        if index_source.is_file():
            index_rows = _lines(
                root,
                ("rev-parse", "--path-format=absolute", "--git-path", "index"),
                "integration-preservation:reapply-index-path",
            )
            if index_rows:
                try:
                    shutil.copy2(index_source, Path(index_rows[0]))
                    restored_any = True
                except OSError:
                    return False
    diff_path = Path(bundle.work_diff)
    if diff_path.is_file() and diff_path.stat().st_size > 0:
        applied = run_git(
            ("apply", "--binary", "--whitespace=nowarn", str(diff_path)),
            cwd=root,
            label="integration-preservation:reapply-work-diff",
        )
        if applied.returncode == 0:
            restored_any = True
        else:
            cached = run_git(
                ("apply", "--cached", "--binary", "--whitespace=nowarn", str(diff_path)),
                cwd=root,
                label="integration-preservation:reapply-work-diff-cached",
            )
            if cached.returncode == 0:
                restored_any = True
    if bundle.resolution_copy is not None:
        resolution_source = Path(bundle.resolution_copy)
        if resolution_source.is_dir():
            git_dir_rows = _lines(
                root,
                ("rev-parse", "--absolute-git-dir"),
                "integration-preservation:reapply-git-dir",
            )
            if git_dir_rows:
                destination = Path(git_dir_rows[0]) / "rr-cache"
                try:
                    if destination.exists():
                        shutil.rmtree(destination)
                    shutil.copytree(resolution_source, destination)
                    restored_any = True
                except OSError:
                    return False
    return restored_any
