from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pytest

_MANIFEST_OPTION = "--ralph-shard-manifest"
_MANIFEST_KEY = pytest.StashKey["ShardManifest"]()
_ROOT_PATH_KEY = pytest.StashKey[Path]()


@dataclass(frozen=True)
class ShardManifest:
    paths: tuple[str, ...]
    _path_set: frozenset[str] = field(init=False, repr=False)
    _path_order: dict[str, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_path_set", frozenset[str](self.paths))
        object.__setattr__(
            self,
            "_path_order",
            dict[str, int]({path: index for index, path in enumerate(self.paths)}),
        )

    def order_for(self, path: str) -> int:
        return self._path_order.get(path, len(self.paths))

    def should_ignore(self, path: str) -> bool:
        if not _is_collectable_test_module(path):
            return False
        return path not in self._path_set


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
        return path.relative_to(root).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return None


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(_MANIFEST_OPTION, action="store", default=None)


def pytest_configure(config: pytest.Config) -> None:
    option: object = config.getoption(_MANIFEST_OPTION)
    if not isinstance(option, str) or not option:
        raise pytest.UsageError(f"{_MANIFEST_OPTION} is required")
    config.stash[_MANIFEST_KEY] = load_shard_manifest(Path(option))
    config.stash[_ROOT_PATH_KEY] = config.rootpath.resolve()


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    relative_path = _project_relative_path(
        collection_path,
        root=config.stash[_ROOT_PATH_KEY],
    )
    if relative_path is None:
        return None
    return True if config.stash[_MANIFEST_KEY].should_ignore(relative_path) else None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    manifest = config.stash[_MANIFEST_KEY]

    def manifest_order(item: pytest.Item) -> int:
        relative_path = _project_relative_path(item.path, root=config.stash[_ROOT_PATH_KEY])
        if relative_path is None:
            return len(manifest.paths)
        return manifest.order_for(relative_path)

    items.sort(key=manifest_order)


pytest.hookimpl(pytest_collection_modifyitems, trylast=True)
