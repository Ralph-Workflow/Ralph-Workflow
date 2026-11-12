from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

PINNED_CONFIG_ARGS: tuple[str, ...] = (
    "-c",
    "rerere.enabled=false",
    "-c",
    "rebase.backend=merge",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
    "-c",
    "core.fsmonitor=false",
)

COMMIT_PIN_CONFIG_ARGS: tuple[str, ...] = (
    "-c",
    "rerere.enabled=false",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
    "-c",
    "core.fsmonitor=false",
)

GIT_DIR_ENV_KEYS: frozenset[str] = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
)

_MIN_PORCELAIN_PIECE_LEN: int = 3

_SELFTEST_ENTRY_COUNT: int = 5


def scrub_git_env(env: dict[str, str] | None) -> dict[str, str]:
    if not env:
        return {}
    return {k: v for k, v in env.items() if k not in GIT_DIR_ENV_KEYS}


def pinned_argv(args: Iterable[str]) -> tuple[str, ...]:
    return (*PINNED_CONFIG_ARGS, *args)


@dataclass(frozen=True)
class PorcelainEntry:
    xy: str
    path: str
    rename_source: str | None = None


def parse_porcelain_z(blob: str) -> list[PorcelainEntry]:
    entries: list[PorcelainEntry] = []
    if not blob:
        return entries
    parts = blob.split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    i = 0
    while i < len(parts):
        piece = parts[i]
        if len(piece) < _MIN_PORCELAIN_PIECE_LEN:
            i += 1
            continue
        xy = piece[:_XY_WIDTH]
        path = (
            piece[_XY_WIDTH + 1 :]
            if (len(piece) > _XY_WIDTH and piece[_XY_WIDTH] == _PATH_SEPARATOR)
            else piece[_XY_WIDTH:]
        )
        if xy[0] in ("R", "C") and i + 1 < len(parts):
            source = path
            i += 1
            dest = parts[i]
            entries.append(PorcelainEntry(xy=xy, path=dest, rename_source=source))
        else:
            entries.append(PorcelainEntry(xy=xy, path=path, rename_source=None))
        i += 1
    return entries


_UNMERGED_XY: frozenset[str] = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})

_XY_WIDTH = 2

_PATH_SEPARATOR = " "


def unmerged_paths_z(blob: str) -> list[PorcelainEntry]:
    return [entry for entry in parse_porcelain_z(blob) if entry.xy in _UNMERGED_XY]


def porcelain_z(repo_root: Path) -> str:
    result = run_git(
        ("status", "--porcelain", "-z", "--untracked-files=no"),
        cwd=repo_root,
        label="hardening:porcelain-z",
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def unmerged_paths_collect(repo_root: Path) -> list[PorcelainEntry]:
    blob = porcelain_z(repo_root)
    if not blob:
        return []
    return unmerged_paths_z(blob)


__all__ = [
    "GIT_DIR_ENV_KEYS",
    "PINNED_CONFIG_ARGS",
    "PorcelainEntry",
    "parse_porcelain_z",
    "pinned_argv",
    "porcelain_z",
    "scrub_git_env",
    "unmerged_paths_collect",
    "unmerged_paths_z",
]


def _selftest() -> None:
    blob = (
        "UUconflicted.txt\0"
        "AAadded-both.txt\0"
        "UDdeleted-by-them.txt\0"
        "R old/name.txt\0new/name.txt\0"
        " Mmodified.txt\0"
    )
    entries = parse_porcelain_z(blob)
    assert len(entries) == _SELFTEST_ENTRY_COUNT, entries
    unmerged = unmerged_paths_z(blob)
    assert [e.path for e in unmerged] == [
        "conflicted.txt",
        "added-both.txt",
        "deleted-by-them.txt",
    ], [e.path for e in unmerged]
    rename = next(e for e in entries if e.xy.startswith("R"))
    assert rename.rename_source == "old/name.txt", rename
    assert rename.path == "new/name.txt", rename
    assert scrub_git_env({"GIT_DIR": "/x", "PATH": "/y"}) == {"PATH": "/y"}
    out = pinned_argv(("rebase", "--", "upstream"))
    assert all(out[i] == "-c" and "=" in out[i + 1] for i in range(0, len(PINNED_CONFIG_ARGS), 2))
    assert out[-2:] == ("--", "upstream")


if __name__ == "__main__":
    _selftest()
    print("hardening self-test: ok")
