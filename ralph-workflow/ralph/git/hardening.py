from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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

UNMERGED_PORCELAIN_XY: frozenset[str] = frozenset(
    {"UU", "AA", "DD", "AU", "UA", "DU", "UD"}
)

_MIN_PORCELAIN_PIECE_LEN: int = 3

_SELFTEST_ENTRY_COUNT: int = 5

_PATH_SEPARATOR = " "

_XY_WIDTH = 2


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


__all__ = [
    "GIT_DIR_ENV_KEYS",
    "PINNED_CONFIG_ARGS",
    "UNMERGED_PORCELAIN_XY",
    "PorcelainEntry",
    "parse_porcelain_z",
    "pinned_argv",
    "scrub_git_env",
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
    if len(entries) != _SELFTEST_ENTRY_COUNT:
        raise AssertionError(f"Expected {_SELFTEST_ENTRY_COUNT} entries, got {len(entries)}")
    rename = next(e for e in entries if e.xy.startswith("R"))
    if rename.rename_source != "old/name.txt":
        raise AssertionError(f"Expected old/name.txt, got {rename.rename_source}")
    if rename.path != "new/name.txt":
        raise AssertionError(f"Expected new/name.txt, got {rename.path}")
    if scrub_git_env({"GIT_DIR": "/x", "PATH": "/y"}) != {"PATH": "/y"}:
        raise AssertionError("scrub_git_env failed")
    out = pinned_argv(("rebase", "--", "upstream"))
    if not all(out[i] == "-c" and "=" in out[i + 1] for i in range(0, len(PINNED_CONFIG_ARGS), 2)):
        raise AssertionError("pinned config args missing")
    if out[-2:] != ("--", "upstream"):
        raise AssertionError("trailing argv mismatch")


if __name__ == "__main__":
    _selftest()
    print("hardening self-test: ok")
