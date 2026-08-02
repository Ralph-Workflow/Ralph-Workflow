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
import tokenize
from dataclasses import dataclass
from io import StringIO
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
_GETATTR_MIN_ARGS = 2


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
    """Return local D3 exception comments with a stated lifecycle contract."""
    try:
        tokens = tokenize.generate_tokens(StringIO(source).readline)
        return {
            token.start[0]
            for token in tokens
            if token.type == tokenize.COMMENT
            and _MARKER_TOKEN in token.string
            and token.string.split(_MARKER_TOKEN, maxsplit=1)[1].strip()
        }
    except tokenize.TokenError:
        return set()


def _has_local_marker(line: int, marker_lines: set[int]) -> bool:
    return line in marker_lines or line - 1 in marker_lines


_SUBPROCESS_CALLS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)


def _module_aliases(tree: ast.Module) -> tuple[set[str], set[str], set[str], dict[str, str]]:
    time_names = _imported_names(tree, module="time", accepted={"time", "sleep"})
    asyncio_names = _imported_names(tree, module="asyncio", accepted={"asyncio", "sleep"})
    observer_names = _observer_aliases(tree)
    subprocess_names = _subprocess_aliases(tree)
    return time_names, asyncio_names, observer_names, subprocess_names


def _observer_aliases(tree: ast.Module) -> set[str]:
    """Return local roots that can construct watchdog observers.

    ``import watchdog`` exposes the same observer constructor as a direct
    ``watchdog.observers`` import.  Recognizing the root package keeps a new
    watch owner from evading P1/P4 enforcement through attribute chaining.
    """
    aliases = _imported_names(
        tree, module="watchdog.observers", accepted={"watchdog.observers", "Observer"}
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == "watchdog"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "watchdog":
            aliases.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == "observers"
            )
    return aliases


def _subprocess_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local subprocess import names to their canonical API names.

    Direct imports retain their canonical member so an alias such as
    ``from subprocess import run as launch`` cannot evade the typed-process
    ownership audit.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "subprocess":
                    aliases[imported.asname or imported.name] = "subprocess"
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for imported in node.names:
                if imported.name in _SUBPROCESS_CALLS:
                    aliases[imported.asname or imported.name] = imported.name
    return aliases


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


def _dynamic_subprocess_launcher(node: ast.Call, subprocess_names: dict[str, str]) -> bool:
    """True for a statically resolved ``getattr(subprocess, launcher)(...)`` call."""
    func = node.func
    if not (
        isinstance(func, ast.Call)
        and isinstance(func.func, ast.Name)
        and func.func.id == "getattr"
        and len(func.args) >= _GETATTR_MIN_ARGS
        and isinstance(func.args[0], ast.Name)
        and subprocess_names.get(func.args[0].id) == "subprocess"
        and isinstance(func.args[1], ast.Constant)
        and isinstance(func.args[1].value, str)
    ):
        return False
    return func.args[1].value in _SUBPROCESS_CALLS


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
    subprocess_names: dict[str, str],
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
        "raw_subprocess_invocation"
        if _dynamic_subprocess_launcher(node, subprocess_names)
        else "raw_observer_construction"
        if (name == "Observer" and (name in observer_names or root in observer_names))
        else "raw_sleep_poll"
        if (
            name == "sleep"
            and (name in time_names or root in time_names or name in asyncio_names or root in asyncio_names)
        )
        else "raw_subprocess_invocation"
        if (
            (isinstance(func, ast.Name) and subprocess_names.get(name) in _SUBPROCESS_CALLS)
            or (
                isinstance(func, ast.Attribute)
                and name in _SUBPROCESS_CALLS
                and root is not None
                and subprocess_names.get(root) == "subprocess"
            )
        )
        else None
    )
    return (kind, _VIOLATION_MESSAGES[kind]) if kind is not None else None


def _scan_module(module_path: Path, rel_path: str) -> list[FilesystemPollingInvocationViolation]:
    try:
        source = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
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
        missing_roots = [
            package_root / root_name
            for root_name in _DEFAULT_PACKAGE_ROOTS
            if not (package_root / root_name).is_dir()
        ]
        if missing_roots:
            return [
                FilesystemPollingInvocationViolation(
                    "missing_production_root",
                    root.relative_to(package_root).as_posix(),
                    0,
                    "expected production root could not be walked; restore it so the "
                    "filesystem polling/invocation audit can fail closed",
                )
                for root in missing_roots
            ]
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
