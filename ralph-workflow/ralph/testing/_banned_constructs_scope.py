from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.testing._banned_constructs_model import _AuditInputError
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Sequence

_SKIP_PARTS = frozenset(
    [
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "site-packages",
        "vendor",
    ]
)
_CONFIG_NAMES = frozenset({"pyproject.toml", "mypy.ini", ".mypy.ini"})


def _skip_path(path: Path) -> bool:
    return any(part in _SKIP_PARTS or part.startswith(".venv") for part in path.parts)


def _resolve_explicit_paths(paths: Sequence[str], base: Path) -> list[Path]:
    found: dict[Path, None] = {}
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve()
        workspace = FsWorkspace(base, allowed_roots=(base, candidate))
        if workspace.is_file(str(candidate)):
            if (candidate.suffix == ".py" or candidate.name in _CONFIG_NAMES) and not _skip_path(
                candidate
            ):
                found[candidate] = None
            continue
        if workspace.is_dir(str(candidate)):
            for nested_name in workspace.iter_files(str(candidate)):
                nested = Path(nested_name)
                if (
                    nested.suffix == ".py"
                    and workspace.is_file(str(nested))
                    and not _skip_path(nested)
                ):
                    found[nested.resolve()] = None
            for name in _CONFIG_NAMES:
                nested = candidate / name
                if workspace.is_file(str(nested)) and not _skip_path(nested):
                    found[nested.resolve()] = None
            continue
        raise _AuditInputError(f"--paths entry is neither a file nor directory: {raw}")
    return sorted(found)


def _git_root(start: Path) -> Path:
    try:
        completed = run_process(
            "git",
            ("rev-parse", "--show-toplevel"),
            options=ProcessRunOptions(cwd=start, capture_output=True),
        )
    except ProcessExecutionError as error:
        raise _AuditInputError(f"cannot locate git repository: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise _AuditInputError(f"cannot locate git repository: {detail}")
    return Path(completed.stdout.strip()).resolve()


def _tracked_python_paths(root: Path) -> list[Path]:
    try:
        completed = run_process(
            "git",
            ("ls-files", "-z", "--", "*.py"),
            options=ProcessRunOptions(cwd=root, capture_output=True),
        )
    except ProcessExecutionError as error:
        raise _AuditInputError(f"cannot enumerate tracked Python files: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise _AuditInputError(f"cannot enumerate tracked Python files: {detail}")
    paths: list[Path] = []
    for relative in completed.stdout.split("\0"):
        if not relative:
            continue
        path = root / relative
        if DEFAULT_FILE_BACKEND.exists(path) and not _skip_path(Path(relative)):
            paths.append(path.resolve())
    return sorted(paths)


def _common_root(paths: Sequence[Path]) -> Path:
    if not paths:
        return Path.cwd().resolve()
    common = paths[0].parent if paths[0].is_file() else paths[0]
    for path in paths[1:]:
        candidate = path.parent if path.is_file() else path
        while common != common.parent and common not in candidate.parents and common != candidate:
            common = common.parent
    return common


def _scan_root(paths: Sequence[Path]) -> Path:
    for path in paths:
        parts = path.parts
        for root in ("ralph", "tests"):
            if root in parts:
                index = parts.index(root)
                return Path(*parts[:index])
    return _common_root(paths)


def _config_paths(paths: Sequence[Path], root: Path) -> list[Path]:
    found = {path for path in paths if path.name in _CONFIG_NAMES}
    for name in _CONFIG_NAMES:
        candidate = root / name
        if candidate.is_file():
            found.add(candidate.resolve())
    return sorted(found)
