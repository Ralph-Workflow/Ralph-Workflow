"""Support helpers for the real-config isolation guard."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import NamedTuple

# Real home and real XDG config home resolved before any test or fixture
# monkeypatches HOME or XDG_CONFIG_HOME.
_REAL_HOME: Path = Path(os.environ.get("HOME") or Path.home()).resolve()
_REAL_XDG_CONFIG_HOME: Path = (
    Path(os.environ["XDG_CONFIG_HOME"]).resolve()
    if os.environ.get("XDG_CONFIG_HOME")
    else (_REAL_HOME / ".config").resolve()
)

_WRITE_FLAGS: int = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | getattr(os, "O_APPEND", 0)


class _ConfigPathSnapshot(NamedTuple):
    """Snapshot record of a guarded configuration file or directory."""

    path: Path
    is_dir: bool
    mtime_ns: int
    size: int
    sha256: str
    mode: int = 0
    ctime_ns: int = 0


class _GuardState:
    """Internal mutable state container avoiding global statements."""

    def __init__(self) -> None:
        self.scanning: bool = False
        self.audit_hook_installed: bool = False
        self.current_test_node: str | None = None
        self.resolved_dirs_cache: tuple[str | None, tuple[Path, ...], tuple[str, ...]] = (
            None,
            (),
            (),
        )
        self.recorded_reads: list[tuple[str | None, Path]] = []


_STATE = _GuardState()


def _compute_file_sha256(path: Path) -> str:
    """Compute sha256 hex digest of a file, returning empty string on read failure."""
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return ""


def _resolve_guarded_config_dirs() -> list[Path]:
    """Resolve real configuration directories to guard against test pollution."""
    override = os.environ.get("RALPH_TEST_GUARDED_CONFIG_DIRS")
    if override:
        return [Path(p).resolve() for p in override.split(os.pathsep) if p]
    dirs: list[Path] = []
    if _REAL_XDG_CONFIG_HOME not in dirs:
        dirs.append(_REAL_XDG_CONFIG_HOME)
    real_dot_config = (_REAL_HOME / ".config").resolve()
    if real_dot_config not in dirs:
        dirs.append(real_dot_config)
    if _REAL_HOME not in dirs:
        dirs.append(_REAL_HOME)
    return dirs


def _get_guarded_dirs_and_prefixes() -> tuple[tuple[Path, ...], tuple[str, ...]]:
    env_val = os.environ.get("RALPH_TEST_GUARDED_CONFIG_DIRS")
    if _STATE.resolved_dirs_cache[0] == env_val and _STATE.resolved_dirs_cache[1]:
        return _STATE.resolved_dirs_cache[1], _STATE.resolved_dirs_cache[2]
    dirs = tuple(_resolve_guarded_config_dirs())
    prefixes: list[str] = []
    for d in dirs:
        resolved_str = str(d.resolve())
        prefixes.append(f"{resolved_str}/ralph-workflow")
        prefixes.append(f"{resolved_str}/.ralph-workflow")
    _STATE.resolved_dirs_cache = (env_val, dirs, tuple(prefixes))
    return dirs, tuple(prefixes)


def _get_guarded_config_dirs_cached() -> tuple[Path, ...]:
    dirs, _ = _get_guarded_dirs_and_prefixes()
    return dirs


def _scan_guarded_config_files(
    guarded_dirs: list[Path] | None = None,
) -> dict[Path, _ConfigPathSnapshot]:
    """Scan and snapshot all existing files matching ralph-workflow* in guarded dirs."""
    prev_scanning = _STATE.scanning
    _STATE.scanning = True
    try:
        if guarded_dirs is None:
            guarded_dirs = _resolve_guarded_config_dirs()
        snapshot: dict[Path, _ConfigPathSnapshot] = {}
        for base_dir in guarded_dirs:
            if not base_dir.exists():
                continue
            for pattern in ("ralph-workflow*", ".ralph-workflow*"):
                try:
                    matches = list(base_dir.glob(pattern))
                except OSError:
                    continue
                for match in matches:
                    try:
                        resolved = match.resolve()
                        if match.is_dir():
                            st = match.stat()
                            snapshot[resolved] = _ConfigPathSnapshot(
                                path=resolved,
                                is_dir=True,
                                mtime_ns=st.st_mtime_ns,
                                size=0,
                                sha256="",
                                mode=st.st_mode,
                                ctime_ns=st.st_ctime_ns,
                            )
                            for child in match.rglob("*"):
                                try:
                                    child_resolved = child.resolve()
                                    child_st = child.stat()
                                    is_directory = child.is_dir()
                                    snapshot[child_resolved] = _ConfigPathSnapshot(
                                        path=child_resolved,
                                        is_dir=is_directory,
                                        mtime_ns=child_st.st_mtime_ns,
                                        size=0 if is_directory else child_st.st_size,
                                        sha256="" if is_directory else _compute_file_sha256(child),
                                        mode=child_st.st_mode,
                                        ctime_ns=child_st.st_ctime_ns,
                                    )
                                except OSError:
                                    pass
                        elif match.is_file():
                            st = match.stat()
                            snapshot[resolved] = _ConfigPathSnapshot(
                                path=resolved,
                                is_dir=False,
                                mtime_ns=st.st_mtime_ns,
                                size=st.st_size,
                                sha256=_compute_file_sha256(match),
                                mode=st.st_mode,
                                ctime_ns=st.st_ctime_ns,
                            )
                    except OSError:
                        pass
        return snapshot
    finally:
        _STATE.scanning = prev_scanning


def _diff_config_snapshots(
    before: dict[Path, _ConfigPathSnapshot],
    after: dict[Path, _ConfigPathSnapshot],
) -> list[str]:
    """Compare before and after snapshots and return a list of violation descriptions."""
    violations: list[str] = []
    created = set(after) - set(before)
    for p in sorted(created):
        violations.append(f"CREATED: {p}")

    deleted = set(before) - set(after)
    for p in sorted(deleted):
        violations.append(f"DELETED: {p}")

    common = set(before) & set(after)
    for p in sorted(common):
        b = before[p]
        a = after[p]
        if b.mode != a.mode:
            violations.append(f"MODIFIED (mode changed): {p} (mode {oct(b.mode)} -> {oct(a.mode)})")
        elif not b.is_dir and not a.is_dir:
            if b.sha256 != a.sha256:
                violations.append(
                    f"MODIFIED (content hash changed): {p} (sha256 {b.sha256} -> {a.sha256})"
                )
            elif b.mtime_ns != a.mtime_ns:
                violations.append(
                    f"MODIFIED (mtime changed): {p} (mtime {b.mtime_ns} -> {a.mtime_ns})"
                )
    return violations


def _parse_raw_path_string(arg0: object) -> str | None:
    """Extract string path from open() first argument."""
    if isinstance(arg0, str):
        return arg0
    if isinstance(arg0, bytes):
        try:
            return os.fsdecode(arg0)
        except Exception:
            return None
    if hasattr(arg0, "__fspath__"):
        try:
            return str(arg0.__fspath__())
        except Exception:
            return None
    return None


def _extract_read_path(args: tuple[object, ...]) -> Path | None:
    """Extract and validate target path if open event is a read on a ralph-workflow file."""
    if not args:
        return None
    s_str = _parse_raw_path_string(args[0])
    if s_str is None or "ralph-workflow" not in s_str:
        return None

    mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
    flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
    if any(c in mode for c in ("w", "a", "x", "+")) or (flags & _WRITE_FLAGS):
        return None

    dirs, prefixes = _get_guarded_dirs_and_prefixes()
    target_str = s_str if s_str.startswith("/") else f"{Path.cwd()}/{s_str}"

    if not any(target_str.startswith(prefix) for prefix in prefixes):
        return None

    path = Path(target_str)
    p = path.resolve()
    for gdir in dirs:
        try:
            rel = p.relative_to(gdir)
        except ValueError:
            continue
        if rel.parts and (
            rel.parts[0].startswith("ralph-workflow") or rel.parts[0].startswith(".ralph-workflow")
        ):
            return p
    return None


def _install_config_guard_audit_hook() -> None:
    if _STATE.audit_hook_installed:
        return
    _STATE.audit_hook_installed = True

    def _hook(event: str, args: tuple[object, ...]) -> None:
        if event != "open" or _STATE.scanning or not args:
            return
        arg0 = args[0]
        if isinstance(arg0, str):
            if "ralph-workflow" not in arg0:
                return
        elif hasattr(arg0, "__fspath__"):
            if "ralph-workflow" not in str(arg0.__fspath__()):
                return
        else:
            return
        p = _extract_read_path(args)
        if p is not None:
            _STATE.recorded_reads.append((_STATE.current_test_node, p))

    sys.addaudithook(_hook)


def _set_current_test_node(node: str | None) -> None:
    _STATE.current_test_node = node


def _get_test_read_violations(start: int, node: str) -> list[Path]:
    return [p for (n, p) in _STATE.recorded_reads[start:] if n == node]


def _read_violations_count() -> int:
    return len(_STATE.recorded_reads)


def _clear_recorded_read_violations() -> list[tuple[str | None, Path]]:
    reads = list(_STATE.recorded_reads)
    _STATE.recorded_reads.clear()
    return reads
