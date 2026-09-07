"""Atomic publication primitives for immutable checkout-install generations."""

import fcntl
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ralph._install_errors import InstallError


class InstallLockUnavailableError(RuntimeError):
    pass


@contextmanager
def install_lock(path: Path) -> Iterator[None]:
    """Acquire the installer-specific advisory lock without waiting."""
    _ensure_private_root(path.parent)
    if path.is_symlink():
        raise InstallLockUnavailableError(f"installer lock must not be a symlink: {path}")
    descriptor = os.open(  # filesystem-write-ok: lock must be created without following a symlink.
        path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    path.chmod(0o600)
    with os.fdopen(  # filesystem-write-ok: exclusively-created descriptor remains open for flock.
        descriptor, "w", encoding="utf-8"
    ) as handle:
        acquired = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                raise InstallLockUnavailableError("another Ralph install is already updating rdev") from error
            yield
        finally:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def create_candidate(staging: Path) -> Path:
    """Allocate one unique disposable candidate directory under ``staging``."""
    _ensure_private_root(staging)
    return Path(tempfile.mkdtemp(prefix="generation-", dir=staging))


def finalize_generation(candidate: Path, generations: Path) -> Path:
    """Atomically turn a fully prepared candidate into an immutable generation."""
    _ensure_private_root(generations)
    generation = generations / candidate.name
    candidate.replace(generation)  # filesystem-write-ok: atomically publish a prepared immutable generation.
    return generation


def publish_generation(
    candidate: Path,
    generations: Path,
    *,
    publish_launcher: Callable[[Path], None],
) -> Path:
    """Publish the launcher only after atomically finalizing the candidate generation."""
    generation = finalize_generation(candidate, generations)
    try:
        publish_launcher(generation)
    except (InstallError, OSError) as error:
        if not isinstance(error, InstallError):
            _remove_tree(generation)
        raise
    return generation


def cleanup_staging(staging: Path) -> None:
    """Discard abandoned candidates; immutable generations are never recovery input."""
    _remove_tree(staging)


def point_current(current: Path, generation: Path) -> None:
    if current.exists() and not current.is_symlink():
        return
    staging = current.with_name(f".{current.name}.next")
    staging.unlink(missing_ok=True)
    staging.symlink_to(generation, target_is_directory=True)
    staging.replace(current)  # filesystem-write-ok: atomically refresh the compatibility generation pointer.


def _ensure_private_root(root: Path) -> None:
    if root.is_symlink():
        raise InstallLockUnavailableError(f"installer root must not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o077:
        root.chmod(0o700)


def _remove_tree(path: Path) -> None:
    if path.exists():
        # filesystem-write-ok: staging and unpublished generations are installer-owned.
        shutil.rmtree(path)
