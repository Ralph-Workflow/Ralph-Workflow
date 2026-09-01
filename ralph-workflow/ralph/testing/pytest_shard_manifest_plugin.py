"""Bound pytest shard collection to an ordered manifest of test modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

_MANIFEST_OPTION = "--ralph-shard-manifest"
_MANIFEST_KEY = pytest.StashKey["ShardManifest"]()


@dataclass(frozen=True)
class ShardManifest:
    """Ordered project-relative test-module paths assigned to one shard."""

    paths: tuple[str, ...]

    def order_for(self, path: str) -> int:
        """Return the declared position of ``path`` in this manifest."""
        try:
            return self.paths.index(path)
        except ValueError:
            return len(self.paths)

    def should_ignore(self, path: str) -> bool:
        """Reject collectable test modules under ``tests/`` not in the shard."""
        if not _is_collectable_test_module(path):
            return False
        return path not in self.paths


def _is_collectable_test_module(path: str) -> bool:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or candidate.parts[0] != "tests":
        return False
    if any(part.startswith("_") for part in candidate.parts[1:-1]):
        return False
    return candidate.suffix == ".py" and (
        candidate.name.startswith("test_") or candidate.name.endswith("_test.py")
    )


def _is_valid_manifest_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        path == candidate.as_posix()
        and not candidate.is_absolute()
        and "." not in candidate.parts
        and ".." not in candidate.parts
        and _is_collectable_test_module(path)
    )


def load_shard_manifest(manifest_path: Path) -> ShardManifest:
    """Read and validate one fail-closed pytest shard manifest."""
    try:
        contents = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise pytest.UsageError(
            f"unable to read Ralph shard manifest {manifest_path}: {exc}"
        ) from exc

    paths = tuple(contents.splitlines())
    if (
        not paths
        or any(not _is_valid_manifest_path(path) for path in paths)
        or len(paths) != len(set(paths))
    ):
        raise pytest.UsageError(f"invalid Ralph shard manifest: {manifest_path}")
    return ShardManifest(paths)


def _project_relative_path(path: Path, *, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the required shard-manifest path option."""
    parser.addoption(_MANIFEST_OPTION, action="store", default=None)


def pytest_configure(config: pytest.Config) -> None:
    """Load the shard manifest before collection starts."""
    option: object = config.getoption(_MANIFEST_OPTION)
    if not isinstance(option, str) or not option:
        raise pytest.UsageError(f"{_MANIFEST_OPTION} is required")
    config.stash[_MANIFEST_KEY] = load_shard_manifest(Path(option))


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Reject non-selected test modules before pytest imports them."""
    relative_path = _project_relative_path(collection_path, root=config.rootpath)
    if relative_path is None:
        return None
    return True if config.stash[_MANIFEST_KEY].should_ignore(relative_path) else None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Restore manifest module order after pytest collection."""
    manifest = config.stash[_MANIFEST_KEY]

    def manifest_order(item: pytest.Item) -> int:
        relative_path = _project_relative_path(item.path, root=config.rootpath)
        if relative_path is None:
            return len(manifest.paths)
        return manifest.order_for(relative_path)

    items.sort(key=manifest_order)


pytest.hookimpl(pytest_collection_modifyitems, trylast=True)
