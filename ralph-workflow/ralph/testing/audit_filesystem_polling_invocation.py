"""Fail-closed ownership audit for polling, watches, and tool invocation.

The filesystem-proportional contract requires new production modules to use the
lifecycle-owned watch and typed process seams.  This AST-only audit rejects raw
poll sleeps, watchdog observer construction, and direct subprocess selection
outside those owners.  A local ``filesystem-poll-ok:`` marker with a non-empty
reason is the only exception route.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_PACKAGE_ROOTS: tuple[str, ...] = ("ralph",)
_DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "agents/invoke/_workspace.py",
        "executor/process.py",
        "process/manager/__init__.py",
        "process/manager/_process_manager.py",
        "testing/audit_filesystem_polling_invocation.py",
    }
)
_MARKER_TOKEN = "filesystem-poll-ok:"


@dataclass(frozen=True)
class FilesystemPollingInvocationViolation:
    """One lifecycle-ownership violation with actionable remediation."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        """Return the stable diagnostic representation."""
        return f"{self.file_path}:{self.line}: {self.kind}: {self.message}"


def _collect_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _is_exempt(rel_path: str, exempt_paths: frozenset[str]) -> bool:
    return rel_path in exempt_paths or any(rel_path.endswith("/" + item) for item in exempt_paths)


def _marker_lines(source: str) -> set[int]:
    return {
        line_number
        for line_number, line in enumerate(source.splitlines(), start=1)
        if _MARKER_TOKEN in line and line.split(_MARKER_TOKEN, maxsplit=1)[1].strip()
    }


def _has_local_marker(line: int, marker_lines: set[int]) -> bool:
    return line in marker_lines or line - 1 in marker_lines


_SUBPROCESS_CALLS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)


def _module_aliases(tree: ast.Module) -> tuple[set[str], set[str], set[str], set[str]]:
    time_names = _imported_names(tree, module="time", accepted={"time", "sleep"})
    asyncio_names = _imported_names(tree, module="asyncio", accepted={"asyncio", "sleep"})
    observer_names = _imported_names(
        tree, module="watchdog.observers", accepted={"watchdog.observers", "Observer"}
    )
    subprocess_names = _imported_names(
        tree, module="subprocess", accepted={"subprocess", *_SUBPROCESS_CALLS}
    )
    return time_names, asyncio_names, observer_names, subprocess_names


def _imported_names(tree: ast.Module, *, module: str, accepted: set[str]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == module and imported.name in accepted
            )
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name in accepted
            )
    return names


def _attribute_root(node: ast.Attribute) -> str | None:
    value = node.value
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


_VIOLATION_MESSAGES: dict[str, str] = {
    "raw_observer_construction": "P1/P4 raw watchdog Observer construction; use lifecycle-owned WorkspaceMonitor",
    "raw_subprocess_invocation": "direct product-owned subprocess choice; route through the typed process executor",
    "raw_sleep_poll": "P3 timer-driven polling; use an injected clock and event/index-driven lifecycle owner",
}


def _violation_for_call(
    node: ast.Call,
    *,
    time_names: set[str],
    asyncio_names: set[str],
    observer_names: set[str],
    subprocess_names: set[str],
) -> tuple[str, str] | None:
    func = node.func
    name = (
        func.id
        if isinstance(func, ast.Name)
        else func.attr
        if isinstance(func, ast.Attribute)
        else ""
    )
    root = _attribute_root(func) if isinstance(func, ast.Attribute) else None
    kind = (
        "raw_observer_construction"
        if (name == "Observer" and (name in observer_names or root in observer_names))
        else "raw_sleep_poll"
        if (
            name == "sleep"
            and (name in time_names or root in time_names or name in asyncio_names or root in asyncio_names)
        )
        else "raw_subprocess_invocation"
        if (name in _SUBPROCESS_CALLS and (name in subprocess_names or root in subprocess_names))
        else None
    )
    return (kind, _VIOLATION_MESSAGES[kind]) if kind is not None else None


def _scan_module(module_path: Path, rel_path: str) -> list[FilesystemPollingInvocationViolation]:
    try:
        source = module_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [
            FilesystemPollingInvocationViolation(
                "unreadable_module",
                rel_path,
                0,
                f"module could not be read ({exc}); audit fails closed",
            )
        ]
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [
            FilesystemPollingInvocationViolation(
                "invalid_module",
                rel_path,
                exc.lineno or 1,
                "module could not be parsed; audit fails closed",
            )
        ]
    markers = _marker_lines(source)
    time_names, asyncio_names, observer_names, subprocess_names = _module_aliases(tree)
    violations: list[FilesystemPollingInvocationViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _has_local_marker(node.lineno, markers):
            continue
        details = _violation_for_call(
            node,
            time_names=time_names,
            asyncio_names=asyncio_names,
            observer_names=observer_names,
            subprocess_names=subprocess_names,
        )
        if details is not None:
            kind, message = details
            violations.append(
                FilesystemPollingInvocationViolation(kind, rel_path, node.lineno, message)
            )
    return violations


def audit_filesystem_polling_invocation(
    package_root: Path,
    *,
    module_paths: Sequence[str] | None = None,
    exempt_paths: frozenset[str] | Sequence[str] | None = None,
) -> list[FilesystemPollingInvocationViolation]:
    """Return lifecycle-ownership violations for every production module.

    New modules participate automatically, unreadable or invalid source fails
    closed, and deviations must name their local lifecycle contract.
    """
    if not package_root.is_dir():
        return [
            FilesystemPollingInvocationViolation(
                "missing_package_root", str(package_root), 0, "package root could not be walked"
            )
        ]
    exempt = frozenset(exempt_paths) if exempt_paths is not None else _DEFAULT_EXEMPT_PATHS
    if module_paths is None:
        candidates = [
            (path, path.relative_to(package_root).as_posix())
            for root_name in _DEFAULT_PACKAGE_ROOTS
            for path in _collect_python_files(package_root / root_name)
        ]
    else:
        candidates = [(package_root / rel_path, rel_path) for rel_path in module_paths]
    violations: list[FilesystemPollingInvocationViolation] = []
    for module_path, rel_path in candidates:
        if not _is_exempt(rel_path, exempt):
            violations.extend(_scan_module(module_path, rel_path))
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit from the package root and print actionable violations."""
    selected = sys.argv[1:] if argv is None else argv
    package_root = Path(selected[0]) if selected else Path(__file__).parent.parent.parent
    violations = audit_filesystem_polling_invocation(package_root)
    if violations:
        print(f"FILESYSTEM POLLING/INVOCATION VIOLATIONS: {len(violations)}")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("filesystem polling/invocation ownership audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
