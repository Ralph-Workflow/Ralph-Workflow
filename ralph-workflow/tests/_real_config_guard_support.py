from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import NamedTuple

REAL_HOME: Path = Path(os.environ.get("HOME") or Path.home()).resolve()
REAL_XDG_CONFIG_HOME: Path = (
    Path(os.environ["XDG_CONFIG_HOME"]).resolve()
    if os.environ.get("XDG_CONFIG_HOME")
    else (REAL_HOME / ".config").resolve()
)
_REAL_HOME = REAL_HOME
_REAL_XDG_CONFIG_HOME = REAL_XDG_CONFIG_HOME
_BUNDLED_AGENTS_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "policy"
    / "defaults"
    / "ralph-workflow-agents.toml"
)
_RESOLVED_BUNDLED_AGENTS_TEMPLATE: Path | None = (
    _BUNDLED_AGENTS_TEMPLATE.resolve() if _BUNDLED_AGENTS_TEMPLATE.exists() else None
)
_REPO_DIR: Path = Path(__file__).resolve().parents[1]
_REPO_DIR_STR: str = str(_REPO_DIR)
_ROOT_DIR: Path = _REPO_DIR.parent
_ROOT_DIR_STR: str = str(_ROOT_DIR)
_REPO_DIR_PREFIX: str = f"{_REPO_DIR_STR}/"
_ROOT_DIR_PREFIX: str = f"{_ROOT_DIR_STR}/"

_WRITE_FLAGS: int = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

_GUARDED_CONFIG_DIRS_ENV = "RALPH_TEST_GUARDED_CONFIG_DIRS"
_GUARDED_CONFIG_DIRS_ENV_KEY = _GUARDED_CONFIG_DIRS_ENV.encode()

_MAX_CACHED_PARENTS = 4096


class _GuardedDirs(NamedTuple):
    env_raw: bytes | None
    dirs: tuple[Path, ...]
    dir_strings: tuple[str, ...]


class _ParentResolution(NamedTuple):
    resolved: str
    identity: tuple[int, int]


class _ConfigPathSnapshot(NamedTuple):
    path: Path
    is_dir: bool
    mtime_ns: int
    size: int
    sha256: str
    mode: int = 0
    ctime_ns: int = 0


class _GuardState:
    def __init__(self) -> None:
        self.scanning: bool = False
        self.audit_hook_installed: bool = False
        self.current_test_node: str | None = None
        self.guarded_dirs: _GuardedDirs = _GuardedDirs(None, (), ())
        self.parent_cache: dict[str, _ParentResolution] = {}
        self.recorded_reads: list[tuple[str | None, Path]] = []
        self.recorded_writes: list[tuple[str | None, Path]] = []


_STATE = _GuardState()


def _env_override_raw() -> bytes | None:
    environ = os.environ
    data: dict[bytes, bytes] | None = getattr(environ, "_data", None)
    if data is None:
        value = environ.get(_GUARDED_CONFIG_DIRS_ENV)
        return os.fsencode(value) if value else None
    raw = data.get(_GUARDED_CONFIG_DIRS_ENV_KEY)
    return raw if raw else None


def _compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return ""


def _resolve_guarded_config_dirs() -> list[Path]:
    return _resolve_guarded_config_dirs_from(_env_override_raw())


def _resolve_guarded_config_dirs_from(env_raw: bytes | None) -> list[Path]:
    if env_raw:
        override = os.fsdecode(env_raw)
        return [Path(p).resolve() for p in override.split(os.pathsep) if p]
    dirs: list[Path] = []
    if REAL_XDG_CONFIG_HOME not in dirs:
        dirs.append(REAL_XDG_CONFIG_HOME)
    real_dot_config = (REAL_HOME / ".config").resolve()
    if real_dot_config not in dirs:
        dirs.append(real_dot_config)
    if REAL_HOME not in dirs:
        dirs.append(REAL_HOME)
    return dirs


def _guarded_dirs(env_raw: bytes | None) -> _GuardedDirs:
    cached = _STATE.guarded_dirs
    if cached.env_raw == env_raw and cached.dirs:
        return cached
    dirs = tuple(_resolve_guarded_config_dirs_from(env_raw))
    resolved = _GuardedDirs(env_raw, dirs, tuple(str(d) for d in dirs))
    _STATE.guarded_dirs = resolved
    return resolved


def _scan_guarded_config_files(
    guarded_dirs: list[Path] | None = None,
) -> dict[Path, _ConfigPathSnapshot]:
    prev_scanning = _STATE.scanning
    _STATE.scanning = True
    try:
        if guarded_dirs is None:
            guarded_dirs = _resolve_guarded_config_dirs()
        snapshot: dict[Path, _ConfigPathSnapshot] = {}
        is_synthetic = _env_override_raw() is not None
        for base_dir in guarded_dirs:
            if not base_dir.exists():
                continue
            patterns = ("*", ".*") if is_synthetic else ("ralph-workflow*", ".ralph-workflow*")
            for pattern in patterns:
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
        if _BUNDLED_AGENTS_TEMPLATE.is_file():
            st = _BUNDLED_AGENTS_TEMPLATE.stat()
            resolved = _BUNDLED_AGENTS_TEMPLATE.resolve()
            snapshot[resolved] = _ConfigPathSnapshot(
                path=resolved,
                is_dir=False,
                mtime_ns=st.st_mtime_ns,
                size=st.st_size,
                sha256=_compute_file_sha256(_BUNDLED_AGENTS_TEMPLATE),
                mode=st.st_mode,
                ctime_ns=st.st_ctime_ns,
            )
        return snapshot
    finally:
        _STATE.scanning = prev_scanning


def _diff_config_snapshots(
    before: dict[Path, _ConfigPathSnapshot],
    after: dict[Path, _ConfigPathSnapshot],
) -> list[str]:
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
    if isinstance(arg0, str):
        return arg0
    if isinstance(arg0, bytes):
        try:
            return os.fsdecode(arg0)
        except Exception:
            return None
    if isinstance(arg0, os.PathLike):
        try:
            return os.fsdecode(arg0)
        except Exception:
            return None
    return None


_SAFE_SYSTEM_PREFIXES: tuple[str, ...] = (
    "/sys/",
    "/dev/",
    "/usr/",
    "/lib/",
    "/lib64/",
    "/etc/",
    f"{sys.prefix}/",
    f"{sys.base_prefix}/",
    f"{_ROOT_DIR_STR}/",
    f"{_REPO_DIR_STR}/",
    f"{_REAL_HOME}/.local/",
    f"{_REAL_HOME}/.cache/",
    f"{Path.home()}/.local/",
    f"{Path.home()}/.cache/",
)


def _is_safe_pre_canonical_path(s_str: str) -> bool:
    if ".." in s_str:
        return False
    return bool(s_str.startswith(_SAFE_SYSTEM_PREFIXES))


def _join_parent(parent: str, name: str) -> str:
    if parent.endswith("/"):
        return parent + name
    return f"{parent}/{name}"


def _resolve_parent_directory(parent_str: str) -> str | None:
    cache = _STATE.parent_cache
    entry = cache.get(parent_str)
    try:
        link_stat = os.lstat(parent_str)
    except (OSError, ValueError):
        return None
    if stat.S_ISLNK(link_stat.st_mode):
        try:
            target_stat = Path(parent_str).stat()
        except (OSError, ValueError):
            return None
        identity = (target_stat.st_dev, target_stat.st_ino)
    else:
        identity = (link_stat.st_dev, link_stat.st_ino)
    if entry is not None and entry.identity == identity:
        return entry.resolved
    resolved = os.path.realpath(parent_str)
    if len(cache) >= _MAX_CACHED_PARENTS:
        cache.clear()
    cache[parent_str] = _ParentResolution(resolved, identity)
    return resolved


def _resolve_fully(target_str: str) -> str | None:
    try:
        return str(Path(target_str).resolve())
    except Exception:
        return None


def _split_final_component(target_str: str) -> tuple[str, str] | None:
    if target_str.startswith("//"):
        return None
    parent_str, separator, final = target_str.rpartition("/")
    if not separator or not final or final in (".", ".."):
        return None
    return (parent_str or "/", final)


def _final_component_is_plain(target_str: str) -> bool:
    try:
        final_stat = os.lstat(target_str)
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return not stat.S_ISLNK(final_stat.st_mode)


def _canonicalize_path(target_str: str) -> str | None:
    split = _split_final_component(target_str)
    if split is None:
        return _resolve_fully(target_str)
    parent_str, final = split
    resolved_parent = _resolve_parent_directory(parent_str)
    if resolved_parent is None:
        return _resolve_fully(target_str)
    if not _final_component_is_plain(target_str):
        return _resolve_fully(target_str)
    return _join_parent(resolved_parent, final)


def _is_protected_canonical_str(canonical: str, env_raw: bytes | None) -> bool:
    if (
        canonical in (_REPO_DIR_STR, _ROOT_DIR_STR)
        or canonical.startswith(_REPO_DIR_PREFIX)
        or canonical.startswith(_ROOT_DIR_PREFIX)
    ):
        return False
    if env_raw is not None:
        return any(
            canonical == guarded or canonical.startswith(f"{guarded}/")
            for guarded in _guarded_dirs(env_raw).dir_strings
        )
    real_home = str(REAL_HOME)
    xdg_config_home = str(REAL_XDG_CONFIG_HOME)
    dot_config = _join_parent(real_home, ".config")
    if canonical == xdg_config_home or canonical.startswith(f"{xdg_config_home}/"):
        return True
    if canonical == dot_config or canonical.startswith(f"{dot_config}/"):
        return True
    if not canonical.startswith(f"{real_home}/"):
        return False
    first = canonical[len(real_home) + 1 :].split("/", 1)[0]
    return first.startswith("ralph-workflow") or first.startswith(".ralph-workflow")


def _extract_guarded_path(event: str, args: tuple[object, ...]) -> Path | None:
    del event
    s_str = _parse_raw_path_string(args[0]) if args else None
    if s_str is None:
        return None
    env_raw = _env_override_raw()
    target_str = s_str if s_str.startswith("/") else _join_parent(str(Path.cwd()), s_str)
    if _is_safe_pre_canonical_path(target_str):
        return None

    canonical = _canonicalize_path(target_str)
    if canonical is None:
        return None
    if not _is_protected_canonical_str(canonical, env_raw):
        return None
    return Path(canonical)


def _extract_read_path(args: tuple[object, ...]) -> Path | None:
    if not args:
        return None
    mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
    flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
    if any(c in mode for c in ("w", "a", "x", "+")) or (flags & _WRITE_FLAGS):
        return None
    return _extract_guarded_path("open", args)


def _extract_write_path(args: tuple[object, ...]) -> Path | None:
    if not args:
        return None
    mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
    flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
    if not (any(c in mode for c in ("w", "a", "x", "+")) or (flags & _WRITE_FLAGS)):
        return None
    return _extract_guarded_path("open", args)


def _install_config_guard_audit_hook() -> None:
    if _STATE.audit_hook_installed:
        return
    _STATE.audit_hook_installed = True

    def _hook(event: str, args: tuple[object, ...]) -> None:
        if event != "open" and not event.startswith("os."):
            return
        if _STATE.scanning or not args:
            return
        if event == "open":
            p = _extract_guarded_path("open", args)
            if p is not None:
                mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
                flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
                is_write = any(c in mode for c in ("w", "a", "x", "+")) or bool(
                    flags & _WRITE_FLAGS
                )
                if is_write:
                    _STATE.recorded_writes.append((_STATE.current_test_node, p))
                else:
                    _STATE.recorded_reads.append((_STATE.current_test_node, p))
        elif event in ("os.remove", "os.unlink", "os.mkdir", "os.rmdir", "os.rename"):
            p = _extract_guarded_path(event, args)
            if p is not None:
                _STATE.recorded_writes.append((_STATE.current_test_node, p))
            if event == "os.rename" and len(args) > 1:
                p2 = _extract_guarded_path(event, (args[1],))
                if p2 is not None:
                    _STATE.recorded_writes.append((_STATE.current_test_node, p2))

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


def _clear_recorded_write_violations() -> list[tuple[str | None, Path]]:
    writes = list(_STATE.recorded_writes)
    _STATE.recorded_writes.clear()
    return writes
