"""Anti-drift guards: shared display formatters have exactly one definition.

The display layer previously drifted by re-defining the same formatter in two
modules (e.g. ``make_badge_text``) and by formatting elapsed time three different
ways. These pins fail if a duplicate definition reappears, forcing a single source.
"""

from __future__ import annotations

import ast
import pathlib
from functools import lru_cache

import pytest

_DISPLAY_ROOT = pathlib.Path(__file__).parent.parent / "ralph" / "display"


@lru_cache(maxsize=1)
def _display_trees() -> tuple[ast.Module, ...]:
    """Parse the fixed display source set once for all anti-drift checks."""
    return tuple(
        ast.parse(path.read_text(encoding="utf-8"))
        for path in _DISPLAY_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


@lru_cache(maxsize=1)
def _display_function_names() -> tuple[str, ...]:
    """Return all display function names after one bounded AST traversal."""
    return tuple(
        node.name
        for tree in _display_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    )


def _function_def_count(name: str) -> int:
    return _display_function_names().count(name)


@pytest.mark.timeout_seconds(10)
def test_make_badge_text_defined_once() -> None:
    assert _function_def_count("make_badge_text") == 1


@pytest.mark.timeout_seconds(10)
def test_format_elapsed_seconds_defined_once() -> None:
    assert _function_def_count("format_elapsed_seconds") == 1


@pytest.mark.timeout_seconds(10)
def test_analysis_decision_badge_defined_once() -> None:
    assert _function_def_count("analysis_decision_badge") == 1


def test_no_inline_elapsed_dot_one_f_formatting() -> None:
    """Elapsed time must go through format_elapsed_seconds, never a raw ``:.1f`` +
    's' literal (the divergence that produced inconsistent rounding)."""
    offenders: list[str] = []
    for path in _DISPLAY_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "elapsed_seconds:.1f" in text:
            offenders.append(str(path.relative_to(_DISPLAY_ROOT.parent)))
    assert offenders == [], f"use format_elapsed_seconds instead of :.1f in {offenders}"
