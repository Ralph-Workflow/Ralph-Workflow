"""Ephemeral writable overlays for MCP exec."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.process.manager._process_manager_runtime import load_psutil_module

if TYPE_CHECKING:
    from collections.abc import Iterator

_GENERATED_DIR_NAMES = (
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".tox",
    ".nox",
)

_OVERLAY_OWNER_FILE = ".ralph-exec-owner.json"
_LEGACY_PRUNE_MAX_AGE_SECONDS = 60 * 60 * 24
_START_TIME_TOLERANCE_S = 1e-6
_PSUTIL = load_psutil_module()
_CURRENT_PROCESS_IDENTITY: list[tuple[int, float | None]] = []


def _relative_path_sort_key(path: Path) -> tuple[int, str]:
    return len(path.parts), str(path)


def _compute_exec_base_str(
    os_name: str,
    local_app_data: str | None,
    home_str: str,
    temp_dir: str,
) -> str:
    """Return the private exec overlay base path for the given environment."""
    if os_name == "nt":
        root = local_app_data if local_app_data is not None else home_str
        return str(Path(root) / "ralph" / "exec")

    home_cache = Path(home_str) / ".cache" / "ralph" / "exec"
    try:
        home_cache_resolved = home_cache.resolve()
        temp_root = Path(temp_dir).resolve()
        if home_cache_resolved.is_relative_to(temp_root):
            return str(Path("/var/tmp") / "ralph" / "exec")
    except Exception:
        pass
    return str(home_cache)


def _get_private_exec_base() -> Path:
    """Return a private per-user directory for exec overlays."""
    base = Path(
        _compute_exec_base_str(
            os.name,
            os.environ.get("LOCALAPPDATA") if os.name == "nt" else None,
            str(Path.home()),
            tempfile.gettempdir(),
        )
    )
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with suppress(Exception):
        base.chmod(0o700)
    return base


def _get_workspace_exec_base(workspace_root: Path) -> Path:
    """Return a per-workspace exec sandbox directory scoped to a single repo.

    Each workspace gets its own cache directory so that sandbox size limits
    are per-instance, not shared across all Ralph processes for the same user.
    """
    private_base = _get_private_exec_base()
    workspace_hash = hashlib.sha256(
        str(workspace_root.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    base = private_base / workspace_hash
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return base


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _current_process_start_time(pid: int) -> float | None:
    if _CURRENT_PROCESS_IDENTITY and _CURRENT_PROCESS_IDENTITY[0][0] == pid:
        return _CURRENT_PROCESS_IDENTITY[0][1]
    if _PSUTIL is None or pid <= 0:
        return None
    try:
        process = _PSUTIL.process_from_pid(pid)
        return float(process.create_time())
    except Exception:
        return None


def _current_process_identity() -> tuple[int, float | None]:
    pid = os.getpid()
    if not _CURRENT_PROCESS_IDENTITY or _CURRENT_PROCESS_IDENTITY[0][0] != pid:
        _CURRENT_PROCESS_IDENTITY[:] = [(pid, _current_process_start_time(pid))]
    return _CURRENT_PROCESS_IDENTITY[0]


def _process_identity_matches(pid: int, started_at: float | None) -> bool:
    current_pid, current_started_at = _current_process_identity()
    if pid == current_pid:
        if started_at is None:
            return True
        if current_started_at is not None:
            return abs(current_started_at - started_at) <= _START_TIME_TOLERANCE_S
        return _pid_is_running(pid)

    is_running = _pid_is_running(pid)
    matches = False
    if is_running:
        if started_at is None:
            matches = True
        else:
            live_started_at = _current_process_start_time(pid)
            matches = live_started_at is None or abs(
                live_started_at - started_at
            ) <= _START_TIME_TOLERANCE_S
    return matches


def _read_owner_metadata(marker: Path) -> tuple[int | None, float | None]:
    if not marker.is_file():
        return None, None
    try:
        raw_payload = cast("object", json.loads(marker.read_text(encoding="utf-8")))
    except Exception:
        return None, None
    if not isinstance(raw_payload, dict):
        return None, None
    payload = cast("dict[str, object]", raw_payload)
    pid = payload.get("pid")
    started_at = payload.get("started_at")
    normalized_pid = pid if isinstance(pid, int) else None
    normalized_started_at = float(started_at) if isinstance(started_at, (int, float)) else None
    return normalized_pid, normalized_started_at


def _overlay_owner_metadata(overlay_dir: Path) -> tuple[int | None, float | None]:
    return _read_owner_metadata(overlay_dir / _OVERLAY_OWNER_FILE)


def _prune_stale_exec_dirs(base: Path) -> None:
    current_time = time.time()
    for child in base.iterdir():
        if not child.is_dir():
            continue
        owner_pid, owner_started_at = _overlay_owner_metadata(child)
        if owner_pid is not None:
            if _process_identity_matches(owner_pid, owner_started_at):
                continue
            with suppress(Exception):
                shutil.rmtree(child, ignore_errors=True)
            continue
        with suppress(OSError):
            age_seconds = current_time - child.stat().st_mtime
            if age_seconds >= _LEGACY_PRUNE_MAX_AGE_SECONDS:
                shutil.rmtree(child, ignore_errors=True)


def _write_overlay_owner_metadata(overlay_dir: Path) -> None:
    pid, started_at = _current_process_identity()
    payload: dict[str, int | float] = {"pid": pid}
    if started_at is not None:
        payload["started_at"] = started_at
    (overlay_dir / _OVERLAY_OWNER_FILE).write_text(
        json.dumps(payload), encoding="utf-8"
    )


_COW_CP_ARGS: tuple[str, ...]
if os.name == "posix" and os.uname().sysname == "Darwin":
    _COW_CP_ARGS = ("-c",)
else:
    _COW_CP_ARGS = ("--reflink=auto",)


def _clonefile_or_copy(src: str, dst: str, *, follow_symlinks: bool = True) -> str:
    try:
        subprocess.run(
            ["cp", *_COW_CP_ARGS, src, dst],
            capture_output=True,
            timeout=10,
            check=True,
        )
        if follow_symlinks:
            shutil.copystat(src, dst, follow_symlinks=True)
        return dst
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)


def _mirror_workspace_rsync(
    source_root: Path, overlay_root: Path,
    excluded_names: list[str],
    *,
    link_dest: Path | None = None,
) -> bool:
    rsync = shutil.which("rsync")
    if rsync is None:
        return False
    excludes = [f"--exclude={n}" for n in excluded_names]
    cmd: list[str] = [rsync, "-a", "--delete", "--copy-links"]
    if link_dest is not None and link_dest.is_dir():
        cmd.extend(["--link-dest", str(link_dest)])
    cmd.extend(excludes)
    cmd.extend([f"{source_root}/", f"{overlay_root}/"])
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def _sync_dir(  # noqa: PLR0912
    src: Path, dst: Path,
    excluded_names: frozenset[str],
    ignored_paths: frozenset[Path],
) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    try:
        with os.scandir(src) as src_entries:
            src_children: list[tuple[str, Path]] = []
            for entry in src_entries:
                src_children.append((entry.name, Path(entry.path)))
    except OSError:
        return
    src_names = {name for name, _ in src_children}

    dst_children: dict[str, Path] = {}
    if dst.exists():
        try:
            with os.scandir(dst) as dst_entries:
                for entry in dst_entries:
                    dst_children[entry.name] = Path(entry.path)
        except OSError:
            pass

    for name, src_child in src_children:
        if name in excluded_names:
            continue

        d = dst / name
        try:
            rel = src_child.resolve().relative_to(src.resolve())
        except ValueError:
            rel = Path(name)
        if rel in ignored_paths or any(
            p.is_relative_to(rel) for p in ignored_paths
        ):
            continue

        if src_child.is_symlink():
            d.unlink(missing_ok=True)
            _clonefile_or_copy(str(src_child), str(d), follow_symlinks=True)
        elif src_child.is_file():
            if name in dst_children and dst_children[name].is_file():
                s_stat = src_child.stat()
                d_stat = dst_children[name].stat()
                if s_stat.st_mtime == d_stat.st_mtime and s_stat.st_size == d_stat.st_size:
                    continue
            _clonefile_or_copy(str(src_child), str(d), follow_symlinks=False)
        elif src_child.is_dir():
            _sync_dir(src_child, d, excluded_names, ignored_paths)

    for name, dst_child in dst_children.items():
        if name not in src_names:
            if dst_child.is_dir():
                shutil.rmtree(dst_child, ignore_errors=True)
            else:
                dst_child.unlink(missing_ok=True)


def _mirror_workspace(
    source_root: Path, overlay_root: Path,
    *,
    link_dest: Path | None = None,
) -> None:
    """Copy the workspace into a private overlay.

    Tries ``rsync -a --delete`` first (fastest, Unix-only),
    then a metadata-based diff copy (cross-platform, only copies
    files whose mtime/size changed), falling back to
    ``shutil.copytree`` with filesystem copy-on-write.

    When *link_dest* is provided and points to an existing directory,
    rsync creates hard links for unchanged files instead of copying
    them, making incremental syncs near-instant.
    """
    ignored_relative_paths = _ignored_workspace_relative_paths(source_root)
    excluded_names = frozenset(_GENERATED_DIR_NAMES) | frozenset(
        str(p.parts[0]) for p in ignored_relative_paths if len(p.parts) > 1
    )
    excluded_paths = frozenset(ignored_relative_paths)
    overlay_root.mkdir(parents=True, exist_ok=True)
    if not _mirror_workspace_rsync(
        source_root, overlay_root, sorted(excluded_names),
        link_dest=link_dest,
    ):
        _sync_dir(source_root, overlay_root, excluded_names, excluded_paths)


def _resolve_gitdir_reference(gitdir_file: Path) -> Path | None:
    try:
        gitdir_text = gitdir_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    gitdir_value = gitdir_text
    if gitdir_text.startswith("gitdir:"):
        gitdir_value = gitdir_text.split(":", 1)[1].strip()
    gitdir_path = Path(gitdir_value)
    if not gitdir_path.is_absolute():
        gitdir_path = (gitdir_file.parent / gitdir_path).resolve()
    return gitdir_path


@functools.lru_cache(maxsize=16)
def _ignored_workspace_relative_paths(source_root: Path) -> tuple[Path, ...]:
    ignored_paths: set[Path] = {Path(".git")}
    source_git = source_root / ".git"
    if source_git.is_dir():
        worktrees_dir = source_git / "worktrees"
        if worktrees_dir.is_dir():
            resolved_source_root = source_root.resolve()
            for entry in worktrees_dir.iterdir():
                if not entry.is_dir():
                    continue
                gitdir_path = _resolve_gitdir_reference(entry / "gitdir")
                if gitdir_path is None:
                    continue
                worktree_root = gitdir_path.parent.resolve()
                if worktree_root == resolved_source_root:
                    continue
                if not worktree_root.is_relative_to(resolved_source_root):
                    continue
                ignored_paths.add(worktree_root.relative_to(resolved_source_root))
    return tuple(sorted(ignored_paths, key=_relative_path_sort_key))


def _resolve_gitdir_pointer(source_git: Path) -> Path:
    gitdir_text = source_git.read_text(encoding="utf-8").strip()
    if not gitdir_text.startswith("gitdir:"):
        raise ValueError(f"{source_git} is not a gitdir pointer")

    gitdir_value = gitdir_text.split(":", 1)[1].strip()
    gitdir_path = Path(gitdir_value)
    if not gitdir_path.is_absolute():
        gitdir_path = (source_git.parent / gitdir_path).resolve()
    return gitdir_path


def _resolve_shared_gitdir(source_gitdir: Path) -> Path:
    commondir = source_gitdir / "commondir"
    if commondir.exists():
        common_value = commondir.read_text(encoding="utf-8").strip()
        common_path = Path(common_value)
        if not common_path.is_absolute():
            common_path = (source_gitdir / common_path).resolve()
        return common_path

    if source_gitdir.parent.name == "worktrees":
        return source_gitdir.parent.parent

    return source_gitdir


def _patch_core_worktree(config_text: str, overlay_root: Path) -> str:
    lines = config_text.splitlines()
    if not lines:
        return f"[core]\n\tworktree = {overlay_root}\n"

    output: list[str] = []
    in_core = False
    core_seen = False
    worktree_written = False

    for line in lines:
        stripped = line.strip()
        is_section = stripped.startswith("[") and stripped.endswith("]")
        if is_section:
            if in_core and not worktree_written:
                output.append(f"\tworktree = {overlay_root}")
                worktree_written = True
            in_core = stripped.lower() == "[core]"
            core_seen = core_seen or in_core
            output.append(line)
            continue

        if in_core and stripped.startswith("worktree ="):
            output.append(f"\tworktree = {overlay_root}")
            worktree_written = True
        else:
            output.append(line)

    if in_core and not worktree_written:
        output.append(f"\tworktree = {overlay_root}")
        worktree_written = True

    if not core_seen:
        if output and output[-1] != "":
            output.append("")
        output.append("[core]")
        output.append(f"\tworktree = {overlay_root}")

    return "\n".join(output) + "\n"


def _write_private_config(source_gitdir: Path, private_gitdir: Path, overlay_root: Path) -> None:
    source_config = source_gitdir / "config"
    if source_config.exists():
        config_text = source_config.read_text(encoding="utf-8")
        private_config = _patch_core_worktree(config_text, overlay_root)
    else:
        private_config = (
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = true\n"
            "\tbare = false\n"
            f"\tworktree = {overlay_root}\n"
        )
    private_gitdir.write_text(private_config, encoding="utf-8")


def _copy_worktree_state(source_gitdir: Path, private_gitdir: Path) -> None:
    for filename in (
        "HEAD",
        "index",
        "MERGE_HEAD",
        "MERGE_MSG",
        "CHERRY_PICK_HEAD",
        "REBASE_HEAD",
        "COMMIT_EDITMSG",
    ):
        source = source_gitdir / filename
        if source.exists():
            shutil.copy2(source, private_gitdir / filename)


def _copy_worktree_refs(shared_gitdir: Path, private_gitdir: Path) -> None:
    source_refs = shared_gitdir / "refs"
    private_refs = private_gitdir / "refs"
    private_refs.mkdir(parents=True, exist_ok=True)
    if source_refs.exists():
        shutil.copytree(source_refs, private_refs, dirs_exist_ok=True)

    packed_refs = shared_gitdir / "packed-refs"
    if packed_refs.exists():
        shutil.copy2(packed_refs, private_gitdir / "packed-refs")


def _write_alternates(shared_gitdir: Path, private_gitdir: Path) -> None:
    alternates = private_gitdir / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(f"{shared_gitdir / 'objects'}\n", encoding="utf-8")


def _setup_private_gitdir(
    source_gitdir: Path,
    overlay_git: Path,
    overlay_root: Path,
    tmp_root: Path,
) -> None:
    """Create a private gitdir backed by shared source objects."""
    shared_gitdir = _resolve_shared_gitdir(source_gitdir)
    private_gitdir = tmp_root / "private-gitdir"

    if private_gitdir.exists():
        shutil.rmtree(private_gitdir)
    private_gitdir.mkdir(parents=True, exist_ok=True)

    _copy_worktree_state(source_gitdir, private_gitdir)
    _copy_worktree_refs(shared_gitdir, private_gitdir)
    _write_alternates(shared_gitdir, private_gitdir)
    _write_private_config(source_gitdir, private_gitdir / "config", overlay_root)

    if overlay_git.is_dir():
        shutil.rmtree(overlay_git)
    else:
        overlay_git.unlink(missing_ok=True)
    overlay_git.write_text(f"gitdir: {private_gitdir}\n", encoding="utf-8")


def _ensure_git_isolation(source_root: Path, overlay_root: Path, tmp_root: Path) -> None:
    """Materialize a lightweight private gitdir backed by shared source objects."""
    source_git = source_root / ".git"
    overlay_git = overlay_root / ".git"
    if source_git.is_dir():
        _setup_private_gitdir(source_git, overlay_git, overlay_root, tmp_root)
        return
    if source_git.is_file():
        try:
            _setup_private_gitdir(
                _resolve_gitdir_pointer(source_git), overlay_git, overlay_root, tmp_root
            )
        except Exception:
            shutil.copy2(source_git, overlay_git)


@contextmanager
def create_ephemeral_overlay(source_root: Path) -> Iterator[Path]:
    """Create a temporary workspace mirror for isolated exec runs."""
    private_base = _get_private_exec_base()
    _prune_stale_exec_dirs(private_base)
    tmpdir_path = tempfile.mkdtemp(dir=private_base)
    try:
        tmpdir = Path(tmpdir_path)
        overlay_root = tmpdir / "ws"
        overlay_root.parent.mkdir(parents=True, exist_ok=True)
        _write_overlay_owner_metadata(tmpdir)
        _mirror_workspace(source_root, overlay_root)
        _ensure_git_isolation(source_root, overlay_root, tmpdir)
        yield overlay_root
    finally:
        with suppress(Exception):
            shutil.rmtree(tmpdir_path, ignore_errors=True)


__all__ = ["create_ephemeral_overlay"]
