"""wt-028-display: single-owner AST guard for the persistent Status Bar lifecycle.

Pins the architectural invariant that the persistent Status Bar has exactly
ONE owner: :class:`ralph.display.parallel_display.ParallelDisplay`. Any future
commit that re-introduces a second ``StatusBar(...)`` constructor site or a
second ``_status_bar.start()`` / ``_status_bar.stop()`` call site MUST fail
this test, keeping the Status Bar lifecycle consolidated onto ``ParallelDisplay``.

Scanned checks (AST-based, no subprocess, no network, no real file I/O):

1. ``StatusBar(...)`` constructor invocations appear in exactly one site:
   ``ralph/display/parallel_display.py:ParallelDisplay.__init__``. No other
   module under ``ralph/display/``, ``ralph/pipeline/``, or ``ralph/cli/`` may
   construct a ``StatusBar``.

2. ``_status_bar.start()`` / ``status_bar.start()`` (or any equivalent
   attribute access pattern) call sites appear in exactly one location:
   ``ralph/display/parallel_display.py:ParallelDisplay.start``. The same
   constraint applies to ``_status_bar.stop()`` / ``status_bar.stop()``,
   which must live in ``ParallelDisplay.stop``.

3. ``ralph/cli/**/*.py`` and ``ralph/runtime/**/*.py`` are forbidden from
   constructing ``StatusBar`` or starting/stopping the composed instance.

The contract is evaluated in one scan so xdist does not repeat the same
source-tree parse on separate workers for each clause.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

_RALPH_DIR = Path(__file__).parent.parent.parent / "ralph"
_SCAN_DIRS = (
    _RALPH_DIR / "display",
    _RALPH_DIR / "pipeline",
    _RALPH_DIR / "cli",
)
_FORBIDDEN_DIRS = (
    _RALPH_DIR / "cli",
    _RALPH_DIR / "runtime",
)
_CONSTRUCTOR_FILE = "parallel_display.py"


@cache
def _scan_targets() -> tuple[Path, ...]:
    """Return every ``*.py`` file under the three scan directories."""
    files: list[Path] = []
    for scan_dir in _SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in sorted(scan_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return tuple(files)


@cache
def _parse(path: Path) -> ast.Module:
    """Return the AST module for ``path``, parsed once and cached."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _rel(path: Path) -> str:
    """Return ``path`` relative to the ralph-workflow package root."""
    return str(path.relative_to(_RALPH_DIR))


def _status_bar_constructor_sites(tree: ast.Module) -> list[int]:
    """Yield the lineno of every ``StatusBar(...)`` constructor invocation.

    Uses ``ast.Call`` nodes only — string literals, ``ast.Name`` references,
    and attribute references that are NOT an invocation are excluded so
    docstrings / type comments / re-exports do not produce false positives.
    """
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name: str | None = None
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        if name == "StatusBar":
            sites.append(node.lineno)
    return sites


def _attribute_call_sites(
    tree: ast.Module,
    *,
    attr: str,
    receiver_names: frozenset[str],
) -> list[int]:
    """Yield the lineno of every ``<receiver>.<attr>(...)`` call site.

    Matches the canonical ``self._status_bar.start()`` /
    ``self.status_bar.stop()`` shape used inside ``ParallelDisplay``. The
    ``receiver_names`` set lists the legal local / attribute-prefix names
    on the left of the ``.<attr>(`` (so the scan does not flag unrelated
    ``.start()`` / ``.stop()`` calls on other objects).
    """
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if not isinstance(callee, ast.Attribute):
            continue
        if callee.attr != attr:
            continue
        receiver = callee.value
        receiver_name: str | None = None
        if isinstance(receiver, ast.Name):
            receiver_name = receiver.id
        elif isinstance(receiver, ast.Attribute):
            receiver_name = receiver.attr
        if receiver_name in receiver_names:
            sites.append(node.lineno)
    return sites


def _site_is_in_method(
    tree: ast.Module,
    lineno: int,
    *,
    class_name: str,
    method_name: str,
) -> bool:
    """Return whether ``lineno`` belongs to the named class method."""
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for child in node.body:
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == method_name
                and child.lineno <= lineno <= (child.end_lineno or child.lineno)
            ):
                return True
    return False


def test_parallel_display_exclusively_owns_status_bar_lifecycle() -> None:
    """All constructor, start, stop, CLI, and runtime ownership clauses hold."""
    violations: list[str] = []
    receiver_names: frozenset[str] = frozenset({"_status_bar", "status_bar"})

    for path in _scan_targets():
        tree = _parse(path)
        for lineno in _status_bar_constructor_sites(tree):
            if path.name == _CONSTRUCTOR_FILE and _site_is_in_method(
                tree,
                lineno,
                class_name="ParallelDisplay",
                method_name="__init__",
            ):
                continue
            violations.append(f"{_rel(path)}:{lineno}: StatusBar(...)")
        for lineno in _attribute_call_sites(
            tree,
            attr="start",
            receiver_names=receiver_names,
        ):
            if path.name == _CONSTRUCTOR_FILE and _site_is_in_method(
                tree,
                lineno,
                class_name="ParallelDisplay",
                method_name="start",
            ):
                continue
            violations.append(f"{_rel(path)}:{lineno}: *.start()")
        for lineno in _attribute_call_sites(
            tree,
            attr="stop",
            receiver_names=receiver_names,
        ):
            if path.name == _CONSTRUCTOR_FILE and _site_is_in_method(
                tree,
                lineno,
                class_name="ParallelDisplay",
                method_name="stop",
            ):
                continue
            violations.append(f"{_rel(path)}:{lineno}: *.stop()")

    forbidden_files: set[Path] = set()
    for forbidden_dir in _FORBIDDEN_DIRS:
        if not forbidden_dir.exists():
            continue
        for path in sorted(forbidden_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            forbidden_files.add(path)

    for path in sorted(forbidden_files - set(_scan_targets())):
        tree = _parse(path)
        violations.extend(
            f"{_rel(path)}:{lineno}: StatusBar(...)"
            for lineno in _status_bar_constructor_sites(tree)
        )
        violations.extend(
            f"{_rel(path)}:{lineno}: *.start()"
            for lineno in _attribute_call_sites(
                tree,
                attr="start",
                receiver_names=receiver_names,
            )
        )
        violations.extend(
            f"{_rel(path)}:{lineno}: *.stop()"
            for lineno in _attribute_call_sites(
                tree,
                attr="stop",
                receiver_names=receiver_names,
            )
        )

    assert not violations, (
        "ParallelDisplay must exclusively own StatusBar construction and "
        "lifecycle calls; CLI and runtime modules may only consume it. "
        "Violations:\n" + "\n".join(violations)
    )
