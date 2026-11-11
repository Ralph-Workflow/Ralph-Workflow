from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
        "tmp",
    }
)

_IDLE_WATCHDOG_OWNER: str = "agents/idle_watchdog/idle_watchdog.py"
_POST_EXIT_WATCHDOG_OWNER: str = "agents/idle_watchdog/_post_exit_watchdog.py"
_FIRE_REASON_OWNERS: frozenset[str] = frozenset(
    {
        _IDLE_WATCHDOG_OWNER,
        _POST_EXIT_WATCHDOG_OWNER,
    }
)
_SUBAGENT_COUNTING_OWNER: str = "agents/idle_watchdog/_subagent_identity.py"
_SUBAGENT_COUNTING_OWNERS: frozenset[str] = frozenset({_SUBAGENT_COUNTING_OWNER})
_SUBAGENT_COUNTING_MARKER: str = "class Subagent"
_SUBAGENT_COUNTING_TYPES: tuple[str, ...] = (
    "SubagentIdentity",
    "SubagentPidRegistry",
)

_LEGACY_BASENAME_FRAGMENT_A: str = "old"
_LEGACY_BASENAME_FRAGMENT_B: str = "watchdog"
_LEGACY_BASENAME_SEPARATOR: str = "_"
_LEGACY_BASENAME_EXTENSION: str = ".py"
_LEGACY_ROOT_WATCHDOG: str = (
    _LEGACY_BASENAME_FRAGMENT_A
    + _LEGACY_BASENAME_SEPARATOR
    + _LEGACY_BASENAME_FRAGMENT_B
    + _LEGACY_BASENAME_EXTENSION
)

_FIRE_REASON_MARKER: str = "WatchdogFireReason"
_CLASS_OWNER_MARKERS: tuple[str, ...] = (
    "class IdleWatchdog",
    "class PostExitWatchdog",
)


@dataclass(frozen=True)
class WatchdogDriftViolation:
    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    )


def _rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _format_snippet(source: str, line: int) -> str:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        return None
    return ".".join(reversed(parts))


def _is_top_level_class(tree: ast.Module, name: str) -> list[ast.ClassDef]:
    return [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]


def _file_constructs_watchdog_fire_reason(tree: ast.Module) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "WatchdogFireReason":
            lines.append(node.lineno)
            continue
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "WatchdogFireReason"
        ):
            lines.append(node.lineno)
    return lines


def _check_legacy_root_watchdog(repo_root: Path) -> list[WatchdogDriftViolation]:
    legacy = repo_root / _LEGACY_ROOT_WATCHDOG
    if legacy.is_file():
        return [
            WatchdogDriftViolation(
                kind="legacy_root_watchdog",
                file_path=_LEGACY_ROOT_WATCHDOG,
                line=0,
                message=(
                    f"{_LEGACY_ROOT_WATCHDOG} at ralph-workflow root must be deleted;"
                    " see docs/agents/watchdog-architecture.md"
                ),
            )
        ]
    return []


def _check_canonical_class_owners(
    package_root: Path,
) -> list[WatchdogDriftViolation]:
    violations: list[WatchdogDriftViolation] = []
    for py_file in _iter_py_files(package_root):
        rel_path = _rel_posix(py_file, package_root)
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(marker in source for marker in _CLASS_OWNER_MARKERS):
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, ValueError):
            continue

        for cls in _is_top_level_class(tree, "IdleWatchdog"):
            if rel_path != _IDLE_WATCHDOG_OWNER:
                violations.extend(
                    [
                        WatchdogDriftViolation(
                            kind="duplicate_idle_watchdog",
                            file_path=rel_path,
                            line=cls.lineno,
                            message=(
                                f"top-level class IdleWatchdog is only allowed at"
                                f" {_IDLE_WATCHDOG_OWNER}; found duplicate here"
                            ),
                        )
                    ]
                )

        for cls in _is_top_level_class(tree, "PostExitWatchdog"):
            if rel_path != _POST_EXIT_WATCHDOG_OWNER:
                violations.extend(
                    [
                        WatchdogDriftViolation(
                            kind="duplicate_post_exit_watchdog",
                            file_path=rel_path,
                            line=cls.lineno,
                            message=(
                                f"top-level class PostExitWatchdog is only allowed at"
                                f" {_POST_EXIT_WATCHDOG_OWNER}; found duplicate here"
                            ),
                        )
                    ]
                )
    return violations


def _check_watchdog_fire_reason_construction(
    package_root: Path,
) -> list[WatchdogDriftViolation]:
    violations: list[WatchdogDriftViolation] = []
    for py_file in _iter_py_files(package_root):
        rel_path = _rel_posix(py_file, package_root)
        if rel_path in _FIRE_REASON_OWNERS:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _FIRE_REASON_MARKER not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, ValueError):
            continue
        for line in _file_constructs_watchdog_fire_reason(tree):
            snippet = _format_snippet(source, line)
            violations.append(
                WatchdogDriftViolation(
                    kind="fire_reason_outside_canonical_owner",
                    file_path=rel_path,
                    line=line,
                    message=(
                        f"WatchdogFireReason constructed outside canonical owner"
                        f" (snippet: {snippet!r})"
                    ),
                )
            )
    return violations


def _file_defines_subagent_counting_class(
    tree: ast.Module,
    class_name: str,
) -> list[ast.ClassDef]:
    return [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    ]


def _check_subagent_counting_outside_owner(
    package_root: Path,
) -> list[WatchdogDriftViolation]:
    violations: list[WatchdogDriftViolation] = []
    for py_file in _iter_py_files(package_root):
        rel_path = _rel_posix(py_file, package_root)
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _SUBAGENT_COUNTING_MARKER not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, ValueError):
            continue
        for class_name in _SUBAGENT_COUNTING_TYPES:
            for cls in _file_defines_subagent_counting_class(tree, class_name):
                if rel_path == _SUBAGENT_COUNTING_OWNER:
                    continue
                violations.append(
                    WatchdogDriftViolation(
                        kind="subagent_counting_outside_owner",
                        file_path=rel_path,
                        line=cls.lineno,
                        message=(
                            f"top-level class {class_name} is only allowed at"
                            f" {_SUBAGENT_COUNTING_OWNER}; found duplicate here"
                        ),
                    )
                )
    return violations


def audit_watchdog_drift(
    package_root: Path,
    repo_root: Path | None = None,
) -> list[WatchdogDriftViolation]:
    if repo_root is None:
        repo_root = package_root.parent

    if not package_root.is_dir():
        return []
    if not repo_root.is_dir():
        return []

    violations: list[WatchdogDriftViolation] = []
    violations.extend(_check_legacy_root_watchdog(repo_root))
    violations.extend(_check_canonical_class_owners(package_root))
    violations.extend(_check_watchdog_fire_reason_construction(package_root))
    violations.extend(_check_subagent_counting_outside_owner(package_root))
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    package_root = Path(argv[0]) if argv else Path(__file__).parent.parent

    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations = audit_watchdog_drift(package_root)

    if violations:
        print(f"WATCHDOG DRIFT VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print(
            f"Fix the drift: delete {_LEGACY_ROOT_WATCHDOG} at the"
            " ralph-workflow root, keep exactly one top-level class"
            " IdleWatchdog at ralph/agents/idle_watchdog/idle_watchdog.py,"
            " keep exactly one top-level class PostExitWatchdog at"
            " ralph/agents/idle_watchdog/_post_exit_watchdog.py,"
            " construct WatchdogFireReason only from those two canonical"
            " owner modules, and keep exactly one top-level class"
            f" SubagentIdentity/SubagentPidRegistry at {_SUBAGENT_COUNTING_OWNER}."
        )
        return 1

    print("watchdog drift audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
