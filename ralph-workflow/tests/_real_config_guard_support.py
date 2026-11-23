from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Mapping

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

_CAPABILITY_STATE_FILE_NAME = "ralph-workflow-capabilities.json"

_MUTATION_CONTENT = "content"
_MUTATION_CREATION = "creation"
_MUTATION_DELETION = "deletion"
_MUTATION_MODE = "mode"
_MUTATION_TIMESTAMP = "timestamp"

_CHANGE_EXPLANATIONS: dict[str, frozenset[str]] = {
    "created": frozenset({_MUTATION_CREATION}),
    "deleted": frozenset({_MUTATION_DELETION}),
    "mode": frozenset({_MUTATION_MODE}),
    "content": frozenset({_MUTATION_CONTENT}),
    "mtime": frozenset({_MUTATION_CONTENT, _MUTATION_TIMESTAMP}),
}

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


class _ConfigChange(NamedTuple):
    path: Path
    kind: str
    message: str


class _GuardState:
    def __init__(self) -> None:
        self.scanning: bool = False
        self.audit_hook_installed: bool = False
        self.current_test_node: str | None = None
        self.guarded_dirs: _GuardedDirs = _GuardedDirs(None, (), ())
        self.parent_cache: dict[str, _ParentResolution] = {}
        self.recorded_reads: list[tuple[str | None, Path]] = []
        self.recorded_writes: list[tuple[str | None, Path]] = []
        self.repo_tree_guard_depth: int = 0


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
                        if base_dir == REAL_HOME and match.is_dir():
                            continue
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


_ALL_RECORDED_MUTATIONS: dict[Path, set[str]] = {}


def _record_in_process_mutation(path: Path, *kinds: str) -> None:
    _ALL_RECORDED_MUTATIONS.setdefault(path, set()).update(kinds)


def _in_process_mutation_kinds(path: Path) -> frozenset[str]:
    return frozenset(_ALL_RECORDED_MUTATIONS.get(path, ()))


def _is_capability_state_file(path: Path) -> bool:
    guarded_dirs = _guarded_dirs(_env_override_raw()).dirs
    return path.name == _CAPABILITY_STATE_FILE_NAME and path.parent in guarded_dirs


def _is_external_capability_state_change(path: Path, kind: str) -> bool:
    if not _is_capability_state_file(path):
        return False
    return not (_in_process_mutation_kinds(path) & _CHANGE_EXPLANATIONS[kind])


def _diff_config_snapshots(
    before: dict[Path, _ConfigPathSnapshot],
    after: dict[Path, _ConfigPathSnapshot],
) -> list[str]:
    changes: list[_ConfigChange] = []
    created = set(after) - set(before)
    for p in sorted(created):
        changes.append(_ConfigChange(p, "created", f"CREATED: {p}"))

    deleted = set(before) - set(after)
    for p in sorted(deleted):
        changes.append(_ConfigChange(p, "deleted", f"DELETED: {p}"))

    common = set(before) & set(after)
    for p in sorted(common):
        b = before[p]
        a = after[p]
        if b.mode != a.mode:
            changes.append(
                _ConfigChange(
                    p,
                    "mode",
                    f"MODIFIED (mode changed): {p} (mode {oct(b.mode)} -> {oct(a.mode)})",
                )
            )
        elif not b.is_dir and not a.is_dir:
            if b.sha256 != a.sha256:
                changes.append(
                    _ConfigChange(
                        p,
                        "content",
                        f"MODIFIED (content hash changed): {p} (sha256 {b.sha256} -> {a.sha256})",
                    )
                )
            elif b.mtime_ns != a.mtime_ns:
                changes.append(
                    _ConfigChange(
                        p,
                        "mtime",
                        f"MODIFIED (mtime changed): {p} (mtime {b.mtime_ns} -> {a.mtime_ns})",
                    )
                )
    return [
        change.message
        for change in changes
        if not _is_external_capability_state_change(change.path, change.kind)
    ]


def _parse_raw_path_string(arg0: object) -> str | None:
    if isinstance(arg0, str):
        return arg0
    if isinstance(arg0, bytes):
        try:
            return os.fsdecode(arg0)
        except (UnicodeDecodeError, TypeError, ValueError):
            return None
    if isinstance(arg0, os.PathLike):
        try:
            return os.fsdecode(arg0)
        except (UnicodeDecodeError, TypeError, ValueError):
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
    except (OSError, RuntimeError, ValueError):
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


_OS_MUTATION_EVENTS: dict[str, tuple[tuple[str, ...], bool]] = {
    "os.remove": ((_MUTATION_DELETION,), False),
    "os.mkdir": ((_MUTATION_CREATION,), False),
    "os.rmdir": ((_MUTATION_DELETION,), False),
    "os.rename": ((_MUTATION_DELETION,), True),
    "os.chmod": ((_MUTATION_MODE,), False),
    "os.utime": ((_MUTATION_TIMESTAMP,), False),
    "os.truncate": ((_MUTATION_CONTENT,), False),
}


def _record_write(path: Path, *kinds: str) -> None:
    _STATE.recorded_writes.append((_STATE.current_test_node, path))
    _record_in_process_mutation(path, *kinds)


def _record_open_event(args: tuple[object, ...]) -> None:
    path = _extract_guarded_path("open", args)
    if path is None:
        return
    mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
    flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
    if not (any(c in mode for c in ("w", "a", "x", "+")) or (flags & _WRITE_FLAGS)):
        _STATE.recorded_reads.append((_STATE.current_test_node, path))
        return
    kinds = [_MUTATION_CONTENT, _MUTATION_TIMESTAMP]
    if flags & os.O_CREAT:
        kinds.append(_MUTATION_CREATION)
    _record_write(path, *kinds)


def _record_os_mutation_event(event: str, args: tuple[object, ...]) -> None:
    entry = _OS_MUTATION_EVENTS.get(event)
    if entry is None:
        return
    kinds, has_destination = entry
    path = _extract_guarded_path(event, args)
    if path is not None:
        _record_write(path, *kinds)
    if has_destination and len(args) > 1:
        destination = _extract_guarded_path(event, (args[1],))
        if destination is not None:
            _record_write(destination, _MUTATION_CREATION, _MUTATION_CONTENT)


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
            _record_open_event(args)
            return
        _record_os_mutation_event(event, args)

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


_PROJECT_LOCAL_CONFIG_NAMES: tuple[str, ...] = (
    "ralph-workflow.toml",
    "mcp.toml",
    "pipeline.toml",
    "artifacts.toml",
)


def _scan_project_local_configs() -> dict[Path, str | None]:
    return {
        path: _compute_file_sha256(path) if path.is_file() else None
        for agent_dir in (_ROOT_DIR / ".agent", _REPO_DIR / ".agent")
        for path in (agent_dir / name for name in _PROJECT_LOCAL_CONFIG_NAMES)
    }


def _diff_project_local_configs(
    before: dict[Path, str | None], after: dict[Path, str | None]
) -> list[str]:
    return [
        f"PROJECT-LOCAL CONFIG {'CREATED' if before[path] is None else 'CHANGED'}: {path}"
        for path in before
        if after[path] != before[path] and after[path] is not None
    ]


_REPO_TREE_SKIPPED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        "node_modules",
    }
)


_REPO_TREE_SKIPPED_RUNTIME_DIRS: frozenset[str] = frozenset({".agent", "tmp"})

_REPO_TREE_SKIPPED_RUNTIME_FILES: frozenset[str] = frozenset({"checkpoint.json"})

_GENERATED_DOCS_BUILD_DIR: Path = Path("docs") / "sphinx" / "_build"


def _repo_tree_entry(path: str) -> tuple[int, int] | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def _scan_repo_tree() -> dict[str, tuple[int, int]]:
    entries: dict[str, tuple[int, int]] = {}
    repo_dir = str(_REPO_DIR)
    for directory, dirnames, filenames in os.walk(repo_dir):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _REPO_TREE_SKIPPED_DIRS
            and not (directory == repo_dir and name in _REPO_TREE_SKIPPED_RUNTIME_DIRS)
            and Path(directory, name) != _REPO_DIR / _GENERATED_DOCS_BUILD_DIR
        ]
        for name in filenames:
            path = f"{directory}/{name}"
            entry = _repo_tree_entry(path)
            if entry is not None:
                entries[path] = entry
    with os.scandir(_ROOT_DIR) as children:
        for child in children:
            if child.name in _REPO_TREE_SKIPPED_RUNTIME_FILES:
                continue
            if child.is_file(follow_symlinks=False):
                entry = _repo_tree_entry(child.path)
                if entry is not None:
                    entries[child.path] = entry
    return entries


def _diff_repo_tree[K: (str, Path)](
    before: Mapping[K, tuple[int, int]], after: Mapping[K, tuple[int, int]]
) -> list[str]:
    created_paths: list[K] = sorted(after.keys() - before.keys())
    changed_paths: list[K] = sorted(
        key for key in after.keys() & before.keys() if after[key] != before[key]
    )
    deleted_paths: list[K] = sorted(before.keys() - after.keys())
    return [
        *(f"REPO TREE CREATED: {path}" for path in created_paths),
        *(f"REPO TREE CHANGED: {path}" for path in changed_paths),
        *(f"REPO TREE DELETED: {path}" for path in deleted_paths),
    ]


def _begin_repo_tree_guard() -> dict[str, tuple[int, int]] | None:
    _STATE.repo_tree_guard_depth += 1
    return _scan_repo_tree() if _STATE.repo_tree_guard_depth == 1 else None


def _end_repo_tree_guard(before: dict[str, tuple[int, int]] | None) -> list[str]:
    _STATE.repo_tree_guard_depth -= 1
    return [] if before is None else _diff_repo_tree(before, _scan_repo_tree())
