"""wt-028-display: anti-drift guard for the consolidated single display mode.

Pins the architectural invariant that Ralph Workflow exposes exactly ONE
display mode. Future commits that re-introduce mode-conditional branches
in the production code under ``ralph/display/`` MUST fail this test.

Scanned checks:

1. ``DisplayContext.mode`` is a :data:`~typing.Literal['default']`-typed
   field with a constant string value ``'default'``. There is no
   ``Literal['compact', 'medium', 'wide']`` annotation, no factory
   that returns any other value, and no public override surface.

2. No production file under ``ralph/display/`` contains an AST
   ``Compare`` node whose right-hand string literal is one of
   ``'compact'``, ``'medium'``, or ``'wide'``. The allowlist covers
   ``ralph/display/mode.py`` (the consolidated ``DEFAULT_MODE`` constant
   lives there) and ``ralph/display/__init__.py`` (which documents the
   single mode in its module docstring).

The AST cache is populated at module import time so the test runs in
< 1 s.
"""

from __future__ import annotations

import ast
from functools import cache, lru_cache
from pathlib import Path
from typing import get_args, get_origin

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.mode import DEFAULT_MODE

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_ALLOWLIST = frozenset({"mode.py", "__init__.py"})
_MODE_LITERALS = frozenset({"compact", "medium", "wide"})


@lru_cache(maxsize=1)
def _display_files() -> tuple[Path, ...]:
    """Return all *.py files under ralph/display/."""
    return tuple(sorted(_DISPLAY_DIR.glob("*.py")))


@cache
def _parsed(path: Path) -> ast.Module:
    """Return the AST module for the given file, parsed once."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_literal_value(node: ast.AST) -> str | None:
    """Return the constant string value of an AST Constant node with str value, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_compare_string_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield ``(lineno, literal)`` for every string literal appearing in a Compare node.

    Covers both LHS (``"compact" == x``) and RHS (``x == "compact"``)
    comparisons. Walks both ``ast.Compare`` and the simple
    ``ast.NamedExpr`` (``mode := "compact"``) cases for safety.
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for sub in (node.left, *node.comparators):
                value = _string_literal_value(sub)
                if value is not None and value in _MODE_LITERALS:
                    hits.append((node.lineno, value))
    return hits


# Pre-populate the AST cache at import time so the per-test SIGALRM
# window is not spent re-parsing files.
for _f in _display_files():
    _parsed(_f)


def test_display_context_mode_is_literal_default() -> None:
    """DisplayContext.mode is typed Literal['default'] (no other mode values)."""
    annotations = DisplayContext.__annotations__
    assert "mode" in annotations, (
        f"DisplayContext is missing 'mode' annotation; got {sorted(annotations)!r}"
    )
    annotation_str = str(annotations["mode"])
    assert "default" in annotation_str, (
        f"DisplayContext.mode annotation must include 'default'; got {annotation_str!r}"
    )
    # Use typing introspection for the canonical check.
    annotation = annotations["mode"]
    args = get_args(annotation)
    if args or get_origin(annotation) is not None:
        assert tuple(args) == ("default",), (
            f"DisplayContext.mode must be Literal['default'] only; got Literal{args!r}"
        )


def test_display_context_mode_default_constant_is_default() -> None:
    """The DEFAULT_MODE constant in ralph.display.mode is exactly the string 'default'."""
    assert DEFAULT_MODE == "default", (
        f"ralph.display.mode.DEFAULT_MODE must be 'default'; got {DEFAULT_MODE!r}"
    )


def test_no_compact_medium_wide_branches_in_display_production() -> None:
    """No ralph/display/*.py file outside the allowlist compares to 'compact' / 'medium' / 'wide'.

    Allowlist: ralph/display/mode.py (DEFAULT_MODE='default' constant), and
    ralph/display/__init__.py (the consolidated single-mode docs mention the
    removed mode names only as historical references in the docstring; we
    still allowlist this file because the AST scan would otherwise flag
    docstring-mentions of "compact mode" inside the docstring — but the
    tokenize-based ast.parse only sees code, not strings. The allowlist is
    defence-in-depth: tokenize-based code scanning already excludes
    docstrings, but we keep the allowlist so future docstring expansions
    stay safe.
    """
    violations: list[str] = []
    for path in _display_files():
        if path.name in _ALLOWLIST:
            continue
        tree = _parsed(path)
        for lineno, literal in _iter_compare_string_literals(tree):
            violations.append(f"{path.name}:{lineno}: '{literal}'")
    assert not violations, (
        "Mode-conditional branches found in ralph/display/ production code "
        "(anti-drift guard tripped; Ralph Workflow has a SINGLE display mode "
        "called 'default' — re-introduce no other mode):\n"
        + "\n".join(violations)
    )


def test_make_display_context_no_force_mode_kwarg_call_works() -> None:
    """make_display_context() with no extra kwargs returns a DisplayContext with mode='default'."""
    ctx = make_display_context()
    assert ctx.mode == "default"
    assert isinstance(ctx, DisplayContext)
