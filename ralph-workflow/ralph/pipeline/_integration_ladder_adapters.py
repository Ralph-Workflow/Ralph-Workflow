"""Production adapters for the integration fallback ladder's seams.

The git-plumbing, filesystem-quarantine and environment-probe adapters
that :mod:`ralph.pipeline.integration_ladder` resolves when a caller
runs a rung without injecting its own seams. Every git mutation goes
through :func:`ralph.git.subprocess_runner.run_git`, which serializes
through the per-repository git command queue; hooks are disabled where
git allows; large dirt is streamed (``git diff --output=`` writes the
patch straight to disk, ``tarfile`` streams entries). The adapters are
kept in this private companion module so
:mod:`ralph.pipeline.integration_ladder` stays under the repo-structure
file-size cap; the public constructors live there.
"""

from __future__ import annotations

import errno
import os
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.pipeline.integration_observation import InProgressKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline._integration_ladder_seams import (
        EnvironmentFailure,
        LandResult,
        RepointResult,
    )

_HOOKS_DISABLED: Final[tuple[str, ...]] = ("-c", "core.hooksPath=/dev/null")


class _ProductionGitPlumbing:
    """Production :data:`GitPlumbing` over the queue-routed git runner."""

    def changed_paths(self, root: Path) -> tuple[str, ...] | None:
        from ralph.git.hardening import parse_porcelain_z
        from ralph.git.subprocess_runner import run_git

        # Config flags MUST precede the subcommand: ``git status ... -c
        # key=val`` makes git exit 129 (unknown switch), and a failed
        # probe must read as UNKNOWN (``None``), never as "clean".
        result = run_git(
            (
                "-c",
                "status.renames=false",
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=all",
            ),
            cwd=root,
            label="integration-ladder:changed-paths",
        )
        if result.returncode != 0:
            return None
        paths: list[str] = []
        for entry in parse_porcelain_z(result.stdout):
            if entry.path.startswith(".agent/"):
                continue  # Ralph's own artifacts are never residue.
            if entry.rename_source is not None and entry.rename_source.startswith(".agent/"):
                continue
            paths.append(entry.path)
        return tuple(paths)

    def head_sha(self, root: Path) -> str | None:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=root,
            label="integration-ladder:head-sha",
        )
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and sha else None

    def target_sha(self, root: Path, target: str) -> str | None:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("rev-parse", "--verify", f"refs/heads/{target}"),
            cwd=root,
            label="integration-ladder:target-sha",
        )
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and sha else None

    def commit_dirt(self, root: Path, head_sha: str, owning_worktree: str | None) -> str | None:
        from ralph.pipeline.auto_integrate_backup_refs import preserve_dirt_commit

        try:
            return preserve_dirt_commit(root, head_sha, owning_worktree)
        except RuntimeError:
            return None

    def ref_is_readable(self, root: Path, ref: str) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("cat-file", "-e", ref),
            cwd=root,
            label="integration-ladder:ref-readable",
        )
        return result.returncode == 0

    def prune_landed_backup_refs(
        self, root: Path, is_landed: Callable[[str], bool]
    ) -> tuple[str, ...]:
        from ralph.pipeline.auto_integrate_backup_refs import (
            prune_verified_landed_backup_refs,
        )

        return prune_verified_landed_backup_refs(root, is_landed)

    def write_patch(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        from ralph.git.subprocess_runner import run_git

        # ``--output`` streams the diff straight to disk: no captured
        # buffer, so multi-GB dirt never lands in memory.
        result = run_git(
            ("diff", "--binary", f"--output={dest}", "HEAD", "--", *paths),
            cwd=root,
            label="integration-ladder:write-patch",
        )
        return result.returncode == 0

    def archive_paths(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        try:
            # filesystem-write-ok: ladder tar sidecar under the Ralph state dir
            with tarfile.open(dest, "w") as archive:
                for path in paths:
                    archive.add(root / path, arcname=path)
        except (OSError, tarfile.TarError):
            return False
        return True

    def restore_tree_to_head(self, root: Path) -> bool:
        from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
        from ralph.git.subprocess_runner import run_git

        read = run_git(
            ("read-tree", "HEAD"),
            cwd=root,
            label="integration-ladder:read-tree-head",
        )
        if read.returncode != 0:
            return False
        checkout = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, *_HOOKS_DISABLED, "checkout-index", "-f", "-a"),
            cwd=root,
            label="integration-ladder:checkout-index-force",
        )
        return checkout.returncode == 0

    def abort_operation(self, root: Path, kind: InProgressKind) -> bool:
        from ralph.git.subprocess_runner import run_git

        command: tuple[str, ...] | None = {
            InProgressKind.REBASE: ("rebase", "--abort"),
            InProgressKind.MERGE: ("merge", "--abort"),
            InProgressKind.CHERRY_PICK: ("cherry-pick", "--abort"),
            InProgressKind.REVERT: ("revert", "--abort"),
            InProgressKind.AM: ("am", "--abort"),
            # A multi-step sequencer owns the state files, not a single
            # operation: ``--quit`` forgets the sequence without committing.
            InProgressKind.SEQUENCER: ("cherry-pick", "--quit"),
        }.get(kind)
        if command is None:
            return False
        result = run_git(
            (*_HOOKS_DISABLED, *command),
            cwd=root,
            label="integration-ladder:abort-operation",
        )
        return result.returncode == 0

    def remove_operation_state(self, root: Path, kind: InProgressKind) -> bool:
        git_dir = self._git_dir(root)
        if git_dir is None:
            return False
        candidates: dict[InProgressKind, tuple[str, ...]] = {
            InProgressKind.REBASE: ("rebase-merge", "rebase-apply"),
            InProgressKind.MERGE: ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE"),
            InProgressKind.CHERRY_PICK: ("CHERRY_PICK_HEAD",),
        }
        removed = False
        for name in candidates.get(kind, ()):
            target = git_dir / name
            try:
                if target.is_dir():
                    import shutil

                    shutil.rmtree(target)
                    removed = True
                elif target.is_file():
                    target.unlink()
                    removed = True
            except OSError:
                continue
        return removed

    def reset_hard(self, root: Path, revision: str) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            (*_HOOKS_DISABLED, "reset", "--hard", revision),
            cwd=root,
            label="integration-ladder:reset-hard",
        )
        return result.returncode == 0

    def clean_untracked(self, root: Path, paths: tuple[str, ...]) -> bool:
        from ralph.git.subprocess_runner import run_git

        args = ("clean", "-fd", "--", *paths) if paths else ("clean", "-fd")
        result = run_git(
            (*_HOOKS_DISABLED, *args),
            cwd=root,
            label="integration-ladder:clean-untracked",
        )
        return result.returncode == 0

    def remove_stale_locks(self, root: Path) -> tuple[str, ...]:
        from ralph.git.lock_recovery import (
            lock_identity,
            lock_path_is_reapable,
            unlink_if_unchanged,
        )

        git_dir = self._git_dir(root)
        if git_dir is None:
            return ()
        removed: list[str] = []
        try:
            locks = sorted(git_dir.rglob("*.lock"))
        except OSError:
            return ()
        for lock_path in locks:
            try:
                if not lock_path_is_reapable(lock_path):
                    continue  # a live holder's lock is never removed
                if unlink_if_unchanged(lock_path, lock_identity(lock_path)):
                    removed.append(str(lock_path.relative_to(git_dir)))
            except OSError:
                continue
        return tuple(removed)

    def recreate_worktree(self, root: Path, branch: str) -> bool:
        from ralph.git.subprocess_runner import run_git

        removed = run_git(
            ("worktree", "remove", "--force", str(root)),
            cwd=root,
            label="integration-ladder:worktree-remove",
        )
        del removed  # best-effort: a missing worktree is fine
        created = run_git(
            ("worktree", "add", str(root), branch),
            cwd=root,
            label="integration-ladder:worktree-recreate",
        )
        return created.returncode == 0

    def set_safe_directory(self, root: Path) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("config", "--global", "--add", "safe.directory", str(Path(root).resolve())),
            cwd=root,
            label="integration-ladder:safe-directory",
        )
        return result.returncode == 0

    def repair_gitdir(self, root: Path) -> bool:
        # A linked worktree whose gitdir target vanished cannot be
        # relinked without the main repository's layout; report whether
        # the gitdir answers at all so the degrade rung can re-observe.
        return self._git_dir(root) is not None

    def create_worktree(self, root: Path, path: Path, target: str) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("worktree", "add", "--detach", str(path), target),
            cwd=root,
            label="integration-ladder:worktree-create",
        )
        return result.returncode == 0

    def apply_preserved(self, root: Path, path: Path, preserved_ref: str) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("cherry-pick", preserved_ref),
            cwd=path,
            label="integration-ladder:apply-preserved",
        )
        return result.returncode == 0

    def land_in_worktree(
        self, root: Path, path: Path, target: str, expected_sha: str
    ) -> LandResult:
        """Compare-and-swap the target ref onto the fresh tip; never overwrite a race.

        Reads the current target sha fresh and moves the ref only while it
        still equals ``expected_sha`` (the sha observed at rung start), via
        an old-value ``update-ref`` (atomic at the ref level, ``--no-deref``
        so a symbolic ref can never redirect the write), then re-verifies.
        A concurrent advance returns ``target-moved`` with NO update issued.
        """
        from ralph.git.subprocess_runner import run_git

        tip = run_git(
            ("rev-parse", "HEAD"),
            cwd=path,
            label="integration-ladder:fresh-tip",
        )
        sha = tip.stdout.strip()
        if tip.returncode != 0 or not sha:
            return "tip-unreadable"
        if sha == expected_sha:
            return "landed-already"
        current = self.target_sha(root, target)
        if current is None:
            return "target-unreadable"
        if current != expected_sha:
            return "target-moved"  # a concurrent writer advanced the target: no overwrite
        landed = run_git(
            ("update-ref", "--no-deref", f"refs/heads/{target}", sha, current),
            cwd=path,
            label="integration-ladder:land-from-fresh",
        )
        # Re-verify after the CAS: a non-zero exit or a ref that does not
        # read back as the tip is the same typed failure.
        if landed.returncode != 0 or self.target_sha(root, target) != sha:
            return "update-failed"
        return "landed"

    def repoint_original(self, root: Path, path: Path, observed_sha: str | None) -> RepointResult:
        """Reset the original checkout onto the fresh result, ONLY when safe.

        The reset runs only when the original worktree still exists and
        its HEAD is where the ladder observed it — or, when the HEAD
        moved, a fresh probe shows the worktree detached and clean (no
        checked-out branch work another owner would lose). Any probe
        failure fails closed; any other state means another
        worktree/agent owns the checkout, reported as a typed not-now
        outcome with the tree untouched.
        """
        from ralph.git.subprocess_runner import run_git

        tip = run_git(
            ("rev-parse", "HEAD"),
            cwd=path,
            label="integration-ladder:fresh-tip-for-repoint",
        )
        sha = tip.stdout.strip()
        if tip.returncode != 0 or not sha:
            return "tip-unreadable"
        # Fail closed: without the observed sha, "is it where we left it"
        # cannot be answered, and an unanswered question is never a yes.
        if observed_sha is None:
            return "head-unreadable"
        unsafe = self._repoint_precheck(root, observed_sha)
        if unsafe is not None:
            return unsafe
        if not self.reset_hard(root, sha):
            return "reset-failed"
        return "repointed"

    def _repoint_precheck(self, root: Path, observed_sha: str) -> RepointResult | None:
        """Return the not-now outcome when resetting ``root`` is unsafe, else ``None``."""
        from ralph.git.subprocess_runner import run_git

        if not (Path(root) / ".git").exists():
            return "worktree-missing"
        current = self.head_sha(root)
        if current is None:
            return "head-unreadable"
        if current == observed_sha:
            return None
        # Fresh probe: only a detached, clean worktree is safe to reset.
        detached = run_git(
            ("rev-parse", "--abbrev-ref", "HEAD"),
            cwd=root,
            label="integration-ladder:repoint-detached-probe",
        )
        paths = self.changed_paths(root)
        if detached.returncode != 0 or paths is None:
            return "head-unreadable"  # probe failure fails closed
        if detached.stdout.strip() != "HEAD" or paths:
            return "head-moved"  # another checkout/agent owns the worktree
        return None

    def remote_reachable(self, root: Path, remote: str) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("ls-remote", "--heads", remote),
            cwd=root,
            label="integration-ladder:remote-probe",
        )
        return result.returncode == 0

    def _git_dir(self, root: Path) -> Path | None:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("rev-parse", "--git-dir"),
            cwd=root,
            label="integration-ladder:git-dir",
        )
        raw = result.stdout.strip()
        if result.returncode != 0 or not raw:
            return None
        git_dir = Path(raw)
        return git_dir if git_dir.is_absolute() else Path(root) / git_dir


class _ProductionFileQuarantine:
    """Production :data:`FileQuarantine` over the real filesystem."""

    def rename_into_quarantine(self, root: Path, quarantine_dir: Path, path: str) -> str | None:
        source = root / path
        dest = quarantine_dir / path
        try:
            os.renames(source, dest)
        except OSError:
            return None
        return str(dest)

    def prune_regenerable_artifacts(self, state_dir: Path) -> tuple[str, ...]:
        pruned: list[str] = []
        try:
            entries = list(state_dir.iterdir())
        except OSError:
            return ()
        for entry in entries:
            if entry.name.endswith(".staging") or entry.name == "tmp":
                try:
                    if entry.is_dir():
                        import shutil

                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                except OSError:
                    continue
                pruned.append(entry.name)
        return tuple(pruned)

    def path_is_readable(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                handle.read(1)
        except OSError:
            return False
        return True

    def alternative_state_dir(self, root: Path) -> Path | None:
        del root  # the alternative is intentionally outside the worktree
        try:
            return Path(tempfile.mkdtemp(prefix="ralph-ladder-state-"))
        except OSError:
            return None


class _ProductionEnvironmentProbe:
    """Production :data:`EnvironmentProbe` over bounded git/fs probes."""

    def failure_class(self, root: Path, *, remote: str) -> EnvironmentFailure | None:
        from ralph.git.subprocess_runner import run_git

        failure: EnvironmentFailure | None = None
        probe = run_git(
            ("rev-parse", "--is-inside-work-tree"),
            cwd=root,
            label="integration-ladder:env-probe",
        )
        if probe.returncode != 0:
            if "safe.directory" in probe.stderr:
                failure = "safe_directory"
            elif not (Path(root) / ".git").exists():
                failure = "missing_gitdir"
        else:
            failure = self._write_failure(root)
            if failure is None:
                remote_probe = run_git(
                    ("ls-remote", "--heads", remote),
                    cwd=root,
                    label="integration-ladder:remote-probe",
                )
                if remote_probe.returncode != 0:
                    failure = "remote_down"
        return failure

    @staticmethod
    def _write_failure(root: Path) -> EnvironmentFailure | None:
        """Return the write-probe failure class (read-only fs / disk full), if any."""
        write_probe = Path(root) / ".agent" / ".ladder-write-probe"
        try:
            write_probe.parent.mkdir(parents=True, exist_ok=True)
            with write_probe.open("wb") as handle:
                handle.write(b"probe")
            write_probe.unlink()
        except OSError as exc:
            if exc.errno == errno.EROFS:
                return "read_only_fs"
            if exc.errno == errno.ENOSPC:
                return "disk_full"
        return None


_PRODUCTION_GIT = _ProductionGitPlumbing()
_PRODUCTION_FS = _ProductionFileQuarantine()
_PRODUCTION_ENV = _ProductionEnvironmentProbe()
