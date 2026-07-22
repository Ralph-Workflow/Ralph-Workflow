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

The AST cache is pre-warmed at module import time (matching the
``tests/display/test_single_mode_anti_drift.py`` pattern) so the per-test
scan runs in well under 1 s.
"""

from __future__ import annotations

import ast
from functools import lru_cache
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
_START_LINE_NUMBER = 1417
_STOP_LINE_NUMBER = 1425
_CTOR_LINE_NUMBER = 531


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=256)
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


# Pre-warm the AST cache at import time so per-test SIGALRM windows
# are not spent re-parsing files.
for _path in _scan_targets():
    _parse(_path)


def test_status_bar_only_instantiated_inside_parallel_display() -> None:
    """``StatusBar(...)`` constructor sites appear ONLY in parallel_display.py.

    The canonical site is ``ralph/display/parallel_display.py:ParallelDisplay.__init__``
    at ``_CTOR_LINE_NUMBER`` (``self._status_bar: StatusBar = StatusBar(self)``). No
    other module under ``ralph/display/``, ``ralph/pipeline/``, or
    ``ralph/cli/`` may construct ``StatusBar``.
    """
    violations: list[str] = []
    for path in _scan_targets():
        tree = _parse(path)
        for lineno in _status_bar_constructor_sites(tree):
            if path.name == _CONSTRUCTOR_FILE and lineno == _CTOR_LINE_NUMBER:
                continue
            violations.append(f"{_rel(path)}:{lineno}: StatusBar(...)")
    assert not violations, (
        "StatusBar constructor invoked outside the canonical site "
        "(ralph/display/parallel_display.py:ParallelDisplay.__init__). "
        "Persistent Status Bar lifecycle must have exactly one owner "
        "(ParallelDisplay). Violations:\n" + "\n".join(violations)
    )


def test_parallel_display_is_only_class_that_starts_status_bar() -> None:
    """``_status_bar.start()`` / ``status_bar.start()`` appear ONLY in
    ``ParallelDisplay.start``.

    The canonical site is ``ralph/display/parallel_display.py:ParallelDisplay.start``
    at line 1382. No other module under ``ralph/display/``, ``ralph/pipeline/``,
    or ``ralph/cli/`` may call ``start()`` on the composed StatusBar.
    """
    receiver_names: frozenset[str] = frozenset({"_status_bar", "status_bar"})
    violations: list[str] = []
    for path in _scan_targets():
        tree = _parse(path)
        for lineno in _attribute_call_sites(
            tree,
            attr="start",
            receiver_names=receiver_names,
        ):
            if path.name == _CONSTRUCTOR_FILE and lineno == _START_LINE_NUMBER:
                continue
            violations.append(f"{_rel(path)}:{lineno}: *.start()")
    assert not violations, (
        "StatusBar.start() invoked outside the canonical site "
        "(ralph/display/parallel_display.py:ParallelDisplay.start). "
        "Persistent Status Bar lifecycle must have exactly one owner. "
        "Violations:\n" + "\n".join(violations)
    )


def test_status_bar_stop_only_inside_parallel_display_stop() -> None:
    """``_status_bar.stop()`` / ``status_bar.stop()`` appear ONLY in
    ``ParallelDisplay.stop``.

    The canonical site is ``ralph/display/parallel_display.py:ParallelDisplay.stop``
    at line 1390. No other module under ``ralph/display/``, ``ralph/pipeline/``,
    or ``ralph/cli/`` may call ``stop()`` on the composed StatusBar.
    """
    receiver_names: frozenset[str] = frozenset({"_status_bar", "status_bar"})
    violations: list[str] = []
    for path in _scan_targets():
        tree = _parse(path)
        for lineno in _attribute_call_sites(
            tree,
            attr="stop",
            receiver_names=receiver_names,
        ):
            if path.name == _CONSTRUCTOR_FILE and lineno == _STOP_LINE_NUMBER:
                continue
            violations.append(f"{_rel(path)}:{lineno}: *.stop()")
    assert not violations, (
        "StatusBar.stop() invoked outside the canonical site "
        "(ralph/display/parallel_display.py:ParallelDisplay.stop). "
        "Persistent Status Bar lifecycle must have exactly one owner. "
        "Violations:\n" + "\n".join(violations)
    )


def test_status_bar_is_not_constructed_in_cli_or_runtime_modules() -> None:
    """``ralph/cli/**/*.py`` and ``ralph/runtime/**/*.py`` are forbidden from
    constructing ``StatusBar`` or starting/stopping the composed instance.

    The persistent Status Bar is owned exclusively by
    ``ParallelDisplay``; CLI / runtime layers must reach it through
    ``pd.status_bar`` (the composed accessor on ``ParallelDisplay``) or
    via ``active.update_status_bar(...)`` rather than constructing or
    directly starting / stopping a ``StatusBar``. This test pins the
    separation between the display-owner and the consumers.
    """
    receiver_names: frozenset[str] = frozenset({"_status_bar", "status_bar"})
    forbidden_files: list[Path] = []
    for forbidden_dir in _FORBIDDEN_DIRS:
        if not forbidden_dir.exists():
            continue
        for path in sorted(forbidden_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            forbidden_files.append(path)

    violations: list[str] = []
    for path in forbidden_files:
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
        "CLI / runtime layers must NOT construct StatusBar or "
        "directly start/stop the composed instance — the persistent "
        "Status Bar is owned exclusively by ParallelDisplay. "
        "Violations:\n" + "\n".join(violations)
    )
