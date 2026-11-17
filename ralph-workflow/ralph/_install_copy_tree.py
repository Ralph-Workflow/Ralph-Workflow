from __future__ import annotations

import ast
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_IGNORED_NAMES = frozenset(
    {
        ".agent",
        ".agents",
        ".claude",
        ".codex",
        ".gemini",
        ".hypothesis",
        ".mypy_cache",
        ".opencode",
        ".pytest_cache",
        ".ruff_cache",
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "tmp",
    }
)


@dataclass(frozen=True)
class SnapshotIdentity:
    source_path: str
    source_commit: str
    version: str


def _read_string_assignment(source: str, name: str) -> str:
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return ""
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        named = any(isinstance(target, ast.Name) and target.id == name for target in targets)
        if named and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return ""


def read_snapshot_identity(snapshot_dir: Path) -> SnapshotIdentity | None:
    package = snapshot_dir / "ralph"
    try:
        build_meta = (package / "_build_meta.py").read_text(encoding="utf-8")
        init = (package / "__init__.py").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return SnapshotIdentity(
        source_path=_read_string_assignment(build_meta, "BUILD_SOURCE_PATH"),
        source_commit=_read_string_assignment(build_meta, "BUILD_SOURCE_COMMIT"),
        version=_read_string_assignment(init, "__version__"),
    )


def copy_install_tree(source: Path, destination: Path) -> Path:

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & _IGNORED_NAMES

    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)
    return destination
