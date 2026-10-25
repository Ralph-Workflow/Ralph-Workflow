"""Seams of the integration fallback ladder: typed vocabularies and protocols.

The :data:`GitPlumbing`, :data:`FileQuarantine` and :data:`EnvironmentProbe`
protocols (with their public aliases) and the closed typed vocabularies one
ladder-rung step speaks in (:data:`LadderOutcome`, :data:`LandResult`,
:data:`RepointResult`, :data:`EnvironmentFailure`) live in this private
companion module so :mod:`ralph.pipeline.integration_ladder` stays under
the repo-structure file-size cap; the public constructors and the
dispatcher stay there and re-export every name here unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.integration_observation import InProgressKind

__all__ = [
    "EnvironmentFailure",
    "EnvironmentProbe",
    "FileQuarantine",
    "GitPlumbing",
    "LadderOutcome",
    "LandResult",
    "RepointResult",
]


# ---------------------------------------------------------------------------
# Typed vocabularies (literal aliases, not classes: the ladder module
# publishes exactly one public top-level class, per the repo-structure
# policy).
# ---------------------------------------------------------------------------

type LadderOutcome = Literal["succeeded", "failed", "impossible", "waiting"]
"""Closed outcome vocabulary of one ladder-rung step.

``succeeded`` — the rung did its work (or there was nothing to do).
``failed`` — the rung ran and could not complete; the caller
re-observes and the decision table escalates.
``impossible`` — observation shows the rung cannot succeed (e.g. a
path cannot be renamed); the caller SKIPS it, never repeats it.
``waiting`` — the environment-degrade rung backed off one tick; the
run keeps going, never exits.
"""

type LandResult = Literal[
    "landed",
    "landed-already",
    "tip-unreadable",
    "target-unreadable",
    "target-moved",
    "update-failed",
]
"""Typed result of one compare-and-swap land of the target ref.

``landed`` — the fresh worktree tip is now the target ref, re-verified
after the update. ``landed-already`` — the target already points at the
fresh tip; nothing to move. ``tip-unreadable`` — the fresh worktree's
HEAD could not be read. ``target-unreadable`` — the current target sha
could not be read (fail closed: no CAS without a fresh old value).
``target-moved`` — the target ref no longer matches the sha observed at
rung start: a concurrent writer advanced it, NO ``update-ref`` was
issued, the caller re-observes. ``update-failed`` — the CAS or its
re-verification did not hold.
"""

type RepointResult = Literal[
    "repointed",
    "worktree-missing",
    "head-moved",
    "head-unreadable",
    "tip-unreadable",
    "reset-failed",
]
"""Typed result of repointing the original checkout at the fresh result.

``repointed`` — the original worktree was reset to the fresh tip.
``worktree-missing`` / ``head-moved`` / ``head-unreadable`` are the
not-now outcomes: the original worktree is gone, or its HEAD is not
where the ladder observed it and a fresh probe shows another
checkout/agent owns it (attached, or dirty), or a probe failed closed.
``tip-unreadable`` — the fresh result itself could not be read.
``reset-failed`` — the safe preconditions held but the reset did not.
"""

type EnvironmentFailure = Literal[
    "read_only_fs",
    "disk_full",
    "safe_directory",
    "missing_gitdir",
    "remote_down",
]
"""Closed vocabulary of environment failure classes the degrade rung adapts to."""


# Seams: git plumbing, filesystem quarantine, environment probe.


@runtime_checkable
class _GitPlumbing(Protocol):
    """The git mutations the fallback ladder performs.

    Every member is a single bounded, queue-routed git operation (the
    production adapter calls :func:`ralph.git.subprocess_runner.run_git`).
    Observation deliberately stays outside: these are the mutating
    verbs a fault-injecting double needs to see.
    """

    def changed_paths(self, root: Path) -> tuple[str, ...] | None:
        """Return every changed (tracked or untracked) path, ``-z`` porcelain.

        ``None`` means the probe FAILED: unknown is never clean, and a
        rung must fail closed on it rather than read "nothing to preserve".
        """
        ...

    def head_sha(self, root: Path) -> str | None:
        """Return the current ``HEAD`` commit sha, or ``None`` when unreadable."""
        ...

    def target_sha(self, root: Path, target: str) -> str | None:
        """Return the current sha of ``refs/heads/<target>``, or ``None`` when unreadable."""
        ...

    def commit_dirt(self, root: Path, head_sha: str, owning_worktree: str | None) -> str | None:
        """Commit the dirt with plumbing; return the preservation ref, or ``None``."""
        ...

    def ref_is_readable(self, root: Path, ref: str) -> bool:
        """True when ``ref`` resolves to an object readable from the repository."""
        ...

    def prune_landed_backup_refs(
        self, root: Path, is_landed: Callable[[str], bool]
    ) -> tuple[str, ...]:
        """Delete backup refs verified landed; return the pruned names."""
        ...

    def write_patch(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        """Write a binary patch of ``paths`` against HEAD to ``dest`` (streamed)."""
        ...

    def archive_paths(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        """Write a tar archive of ``paths`` to ``dest`` (streamed)."""
        ...

    def restore_tree_to_head(self, root: Path) -> bool:
        """Force index and worktree back to ``HEAD`` with read-tree/checkout-index."""
        ...

    def abort_operation(self, root: Path, kind: InProgressKind) -> bool:
        """Abort the in-progress operation of ``kind``; report whether it ran."""
        ...

    def remove_operation_state(self, root: Path, kind: InProgressKind) -> bool:
        """Delete the operation's state files when abort failed; report cleaned."""
        ...

    def reset_hard(self, root: Path, revision: str) -> bool:
        """``git reset --hard <revision>`` with hooks disabled; report success."""
        ...

    def clean_untracked(self, root: Path, paths: tuple[str, ...]) -> bool:
        """``git clean -fd`` for the blocking untracked ``paths``; report success."""
        ...

    def remove_stale_locks(self, root: Path) -> tuple[str, ...]:
        """Remove lock files whose owner is proven dead; return removed names."""
        ...

    def recreate_worktree(self, root: Path, branch: str) -> bool:
        """Recreate the worktree at ``root`` checked out on ``branch`` from scratch."""
        ...

    def set_safe_directory(self, root: Path) -> bool:
        """Add the owned path to the global ``safe.directory`` config."""
        ...

    def repair_gitdir(self, root: Path) -> bool:
        """Best-effort repair of a missing/broken gitdir link; report usable."""
        ...

    def create_worktree(self, root: Path, path: Path, target: str) -> bool:
        """Create a fresh worktree at ``path`` from ``target`` (detached HEAD)."""
        ...

    def apply_preserved(self, root: Path, path: Path, preserved_ref: str) -> bool:
        """Apply the preserved work (cherry-pick) inside the fresh worktree."""
        ...

    def land_in_worktree(
        self, root: Path, path: Path, target: str, expected_sha: str
    ) -> LandResult:
        """Compare-and-swap ``refs/heads/<target>`` from ``expected_sha`` to the fresh tip.

        The current target sha is read fresh; the ref moves only when it
        still equals ``expected_sha`` (the sha observed at rung start), via
        an old-value ``update-ref``, and the result is re-verified. A
        concurrent advance reports ``target-moved`` with NO update issued.
        """
        ...

    def repoint_original(self, root: Path, path: Path, observed_sha: str | None) -> RepointResult:
        """Reset the original checkout to the fresh result, ONLY when it is safe.

        Runs only when the original worktree still exists and its HEAD is
        where the ladder observed it — or a fresh probe shows it detached
        and clean; anything else (another checkout/agent owns it, a probe
        fails) is a typed not-now outcome, never a reset.
        """
        ...

    def remote_reachable(self, root: Path, remote: str) -> bool:
        """True when ``remote`` answers a bounded ls-remote probe."""
        ...


GitPlumbing = _GitPlumbing
"""Public alias of the git-plumbing seam; see the module docstring."""


@runtime_checkable
class _FileQuarantine(Protocol):
    """Filesystem seam of the fallback ladder (rename, prune, verify)."""

    def rename_into_quarantine(self, root: Path, quarantine_dir: Path, path: str) -> str | None:
        """Rename ``root/path`` into ``quarantine_dir``; return the new location."""
        ...

    def prune_regenerable_artifacts(self, state_dir: Path) -> tuple[str, ...]:
        """Delete Ralph's own regenerable artifacts under ``state_dir``."""
        ...

    def path_is_readable(self, path: Path) -> bool:
        """True when ``path`` exists and one byte can be read from it."""
        ...

    def alternative_state_dir(self, root: Path) -> Path | None:
        """Return a different writable temp/work location, or ``None``."""
        ...


FileQuarantine = _FileQuarantine
"""Public alias of the filesystem-quarantine seam."""


@runtime_checkable
class _EnvironmentProbe(Protocol):
    """Probe that classifies the environment failure, if any, at ``root``."""

    def failure_class(self, root: Path, *, remote: str) -> EnvironmentFailure | None:
        """Return the environment failure class at ``root``, or ``None`` when healthy."""
        ...


EnvironmentProbe = _EnvironmentProbe
"""Public alias of the environment-probe seam."""
