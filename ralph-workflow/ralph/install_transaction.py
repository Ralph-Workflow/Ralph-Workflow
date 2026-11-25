import fcntl
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil

from ralph._install_errors import InstallError


class InstallLockUnavailableError(RuntimeError):
    pass


@contextmanager
def install_lock(path: Path) -> Iterator[None]:
    _ensure_private_root(path.parent)
    if path.is_symlink():
        raise InstallLockUnavailableError(f"installer lock must not be a symlink: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    path.chmod(0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        acquired = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                raise InstallLockUnavailableError(
                    "another Ralph install is already updating rdev"
                ) from error
            yield
        finally:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def create_candidate(staging: Path) -> Path:
    _ensure_private_root(staging)
    return Path(tempfile.mkdtemp(prefix="generation-", dir=staging))


def finalize_generation(candidate: Path, generations: Path) -> Path:
    _ensure_private_root(generations)
    generation = generations / candidate.name
    candidate.replace(generation)
    return generation


def publish_generation(
    candidate: Path,
    generations: Path,
    *,
    publish_launcher: Callable[[Path], None],
) -> Path:
    generation = finalize_generation(candidate, generations)
    try:
        publish_launcher(generation)
    except (InstallError, OSError) as error:
        if not isinstance(error, InstallError):
            _remove_tree(generation)
        raise
    return generation


GENERATION_PREFIX = "generation-"

RETAINED_GENERATIONS = 2


def running_generation_names() -> frozenset[str]:
    names: set[str] = set()
    for process in psutil.process_iter():
        try:
            cmdline = process.cmdline()
        except (psutil.Error, OSError):
            continue
        for part in cmdline:
            names.update(
                segment for segment in Path(part).parts if segment.startswith(GENERATION_PREFIX)
            )
    return frozenset(names)


def _modified_at(path: Path) -> float:
    return path.stat().st_mtime


def _generation_dirs(generations: Path) -> list[Path]:
    try:
        entries = list(generations.iterdir())
    except OSError:
        return []
    return [
        path
        for path in entries
        if path.name.startswith(GENERATION_PREFIX) and path.is_dir() and not path.is_symlink()
    ]


def prune_generations(
    generations: Path,
    *,
    keep: Path,
    in_use: Callable[[], frozenset[str]] = running_generation_names,
    retain: int = RETAINED_GENERATIONS,
) -> tuple[Path, ...]:
    candidates = sorted(_generation_dirs(generations), key=_modified_at, reverse=True)
    retained = {keep.name, *(path.name for path in candidates[:retain])}
    busy = in_use()
    removed: list[Path] = []
    for path in candidates:
        if path.name in retained or path.name in busy:
            continue
        try:
            _remove_tree(path)
        except OSError:
            continue
        removed.append(path)
    return tuple(removed)


def cleanup_staging(staging: Path) -> None:
    _remove_tree(staging)


def point_current(current: Path, generation: Path) -> None:
    if current.exists() and not current.is_symlink():
        return
    staging = current.with_name(f".{current.name}.next")
    staging.unlink(missing_ok=True)
    staging.symlink_to(generation, target_is_directory=True)
    staging.replace(current)


def _ensure_private_root(root: Path) -> None:
    if root.is_symlink():
        raise InstallLockUnavailableError(f"installer root must not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o077:
        root.chmod(0o700)


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
