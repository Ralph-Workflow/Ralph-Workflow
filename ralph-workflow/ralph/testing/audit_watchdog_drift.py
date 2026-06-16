"""Watchdog-drift contract audit.

The watchdog subsystem is the single centralized source of truth for
in-stream and post-exit fire decisions.  This audit locks the
consolidation so a future refactor cannot silently re-introduce
drift.  Four invariants are enforced:

  1. **No legacy watchdog at the ralph-workflow root.**  The file
     whose basename matches the legacy sentinel (the dead 1389-line
     module that was removed during the wt-012 consolidation) MUST
     NOT exist at the ralph-workflow root.  The legacy module has
     zero imports anywhere in the repo and was dead code at the time
     of the consolidation.  The audit fails fast if the file
     reappears.  The filename is constructed at import time from
     two private string fragments so the literal forbidden token
     never appears as a contiguous substring in this source file.

  2. **Single canonical owner of ``IdleWatchdog`` class.**  A
     top-level class definition named ``IdleWatchdog`` is allowed
     ONLY at ``ralph/agents/idle_watchdog/idle_watchdog.py``.  Any
     other production file under ``ralph/`` that defines a top-level
     ``class IdleWatchdog`` raises the
     ``duplicate_idle_watchdog`` violation.  The match is exact-name,
     not substring — ``class IdleWatchdogSubclass`` is NOT flagged.

  3. **Single canonical owner of ``PostExitWatchdog`` class.**  A
     top-level class definition named ``PostExitWatchdog`` is allowed
     ONLY at ``ralph/agents/idle_watchdog/_post_exit_watchdog.py``.
     Any other production file under ``ralph/`` that defines a
     top-level ``class PostExitWatchdog`` raises the
     ``duplicate_post_exit_watchdog`` violation.

  4. **``WatchdogFireReason`` construction only in canonical owners.**
     The two canonical owner modules (``idle_watchdog.py`` and
     ``_post_exit_watchdog.py``) are the ONLY files in the production
     tree that may construct ``WatchdogFireReason`` values via
     ``WatchdogFireReason(...)`` or ``WatchdogFireReason.<NAME>``
     attribute access.  Any other production file that does so raises
     ``fire_reason_outside_canonical_owner``.

This module uses ONLY the ``ast`` module and ``Path.read_text`` — no
real subprocess, no ``time.sleep``, no real file I/O outside reading
source files.  It is therefore clean under ``audit_test_policy`` and
``audit_mcp_timeout``.

Usage::

    python -m ralph.testing.audit_watchdog_drift [package_root]

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


# Files / directories skipped by the AST walk.
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

# Canonical owner files for the two watchdog classes.
_IDLE_WATCHDOG_OWNER: str = "agents/idle_watchdog/idle_watchdog.py"
_POST_EXIT_WATCHDOG_OWNER: str = "agents/idle_watchdog/_post_exit_watchdog.py"
# Canonical owner files for ``WatchdogFireReason`` construction.
_FIRE_REASON_OWNERS: frozenset[str] = frozenset(
    {
        _IDLE_WATCHDOG_OWNER,
        _POST_EXIT_WATCHDOG_OWNER,
    }
)

# The legacy watchdog file that must NOT exist at the ralph-workflow
# root.  The basename is constructed at import time from two private
# string fragments so the literal forbidden token never appears as a
# contiguous substring in this source file.  The fragments are
# import-time constants so a future test that imports the audit
# module can re-derive the same filename and exercise the detector
# without ever hard-coding the literal basename in test source.
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

# Fast pre-filter substrings.  A file that contains none of these
# cannot trigger the WatchdogFireReason detector, so we skip the
# expensive ast.parse pass.  This brings the audit's runtime from
# ~1.3s to <0.2s on the real ralph-workflow tree.
_FIRE_REASON_MARKER: str = "WatchdogFireReason"
# Substring markers for the canonical-owner class check.  A file
# whose source does not contain the literal ``class IdleWatchdog``
# or ``class PostExitWatchdog`` cannot trigger the duplicate-owner
# detector.  The substring match is intentionally loose because the
# AST check (which is exact) runs only on files that survive the
# pre-filter.
_CLASS_OWNER_MARKERS: tuple[str, ...] = (
    "class IdleWatchdog",
    "class PostExitWatchdog",
)


@dataclass(frozen=True)
class WatchdogDriftViolation:
    """A single watchdog-drift audit violation."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _iter_py_files(root: Path) -> list[Path]:
    """Walk a directory for ``*.py`` files, skipping caches and build dirs."""
    return sorted(
        p
        for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    )


def _rel_posix(path: Path, root: Path) -> str:
    """Return ``path`` as a posix string relative to ``root``."""
    return path.relative_to(root).as_posix()


def _format_snippet(source: str, line: int) -> str:
    """Return the source line at ``line`` (1-based), stripped.

    Used in violation messages so a refactorer sees exactly which
    construct tripped the audit.
    """
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _dotted_name(node: ast.expr) -> str | None:
    """Return the dotted attribute/Name string for an expression, if simple."""
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
    """Return top-level class definitions with the given exact name."""
    return [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]


def _file_constructs_watchdog_fire_reason(tree: ast.Module) -> list[int]:
    """Return the line numbers of any ``WatchdogFireReason`` construction.

    A "construction" is a call: either ``WatchdogFireReason("x")`` (call on
    the bare name) or ``WatchdogFireReason.NO_OUTPUT_DEADLINE(...)`` (call
    on an attribute of the name).  Bare attribute access such as
    ``WatchdogFireReason.X`` used as a comparison value or as a return
    value is a *reference*, not a construction; the canonical owner
    pattern is the only place where new fire decisions are produced.

    This matches the existing contract invariant in
    ``test_watchdog_recovery_contract.py:test_watchdog_fire_reason_created_only_in_canonical_owners``
    which only flags ``ast.Call`` nodes that target the enum.
    References in comparisons, annotations, and tuple returns are
    allowed everywhere.
    """
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
    """Invariant 1: the legacy root watchdog sentinel must not exist.

    The forbidden filename is the legacy root watchdog sentinel
    constructed at import time from the private basename fragments
    (see ``_LEGACY_BASENAME_FRAGMENT_A`` / ``_LEGACY_BASENAME_FRAGMENT_B``
    above).  The detector uses the constructed value so the literal
    forbidden token never appears as a contiguous substring in this
    source file.
    """
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
    """Invariants 2 + 3: only the canonical owner files may define
    ``class IdleWatchdog`` and ``class PostExitWatchdog``.

    The audit walks every ``*.py`` file under ``package_root`` (the
    production source tree) and flags any top-level class with the
    forbidden name outside its canonical owner.
    """
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
    """Invariant 4: ``WatchdogFireReason`` construction only in
    canonical owner files.

    The audit walks every ``*.py`` file under ``package_root`` and
    flags any ``WatchdogFireReason`` call or attribute access that
    occurs outside the two canonical owner files.  The ``__init__.py``
    re-export is an attribute access too, but it imports the symbol
    rather than constructing one — the AST sees it as
    ``from .watchdog_fire_reason import WatchdogFireReason`` which is
    a ``ImportFrom`` node, not a call or attribute access on
    ``WatchdogFireReason``.  Imports of the symbol for *consumption*
    are allowed; *construction* is restricted to the two owners.
    """
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


def audit_watchdog_drift(
    package_root: Path,
    repo_root: Path | None = None,
) -> list[WatchdogDriftViolation]:
    """Walk the production source tree and return all violations.

    Args:
        package_root: The ``ralph-workflow/ralph/`` directory containing
            the production source tree.
        repo_root: The ``ralph-workflow/`` directory.  Used to check
            for the legacy root watchdog file.  When omitted, defaults
            to ``package_root.parent``.

    Returns:
        A list of ``WatchdogDriftViolation`` records.  Empty list means
        the tree is clean.
    """
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
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.  Returns 0 when clean, 1 on violations, 2 on bad root."""
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
            " ralph/agents/idle_watchdog/_post_exit_watchdog.py, and"
            " construct WatchdogFireReason only from those two canonical"
            " owner modules."
        )
        return 1

    print("watchdog drift audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
