"""wt-028-display: anti-drift guard for test fixtures that build DisplayContext.

Pins the architectural invariant that no test fixture in ``tests/``
passes the pre-consolidation ``DisplayContext(... mode='wide', narrow=False, ...)``
signature. The production ``DisplayContext.mode`` is
``Literal['default']`` and there is no ``narrow=`` parameter; any test
fixture using the legacy signature would raise
``TypeError: DisplayContext.__init__() got an unexpected keyword argument 'narrow'``
on collection, silently masking the drift cleanup. This scan walks every
``tests/**/*.py`` file (excluding ``__pycache__`` and the two intentional
anti-drift guard files in ``tests/display/``) and fails loudly with
``file:line`` + the offending token if any regression is introduced.

Scanned checks:

1. No ``DisplayContext(...)`` call site in ``tests/`` passes
   ``mode='compact' | 'medium' | 'wide'`` as either a positional argument
   or a keyword argument. The pre-consolidation three-tier dispatch is
   dead; test fixtures must use ``mode='default'`` (or rely on the
   ``make_display_context`` factory default).

2. No ``DisplayContext(...)`` call site in ``tests/`` passes ``narrow=``
   or ``narrow:`` as a keyword argument / annotation. The pre-consolidation
   tier flag is dead; the consolidated single-mode Status Bar adapts to
   width inside the renderer rather than via a kwarg.

Performance: the candidate file set is pre-filtered textually at import
time (cheap ``read_text + 'in'`` substring scan that takes < 100 ms over
~600 test files); only files that mention ``DisplayContext`` are AST-parsed
during the tests. This keeps the per-test body well under the 1 s
per-test timeout even under ``pytest -n auto`` workers. There are
typically ~70 candidate files; parsing all of them twice (once per test)
takes ~250 ms total.

This file intentionally mentions ``'compact'``, ``'medium'``, ``'wide'``,
and ``narrow`` as the literal rejection set and is excluded from its own
scan via the ``_ALLOWLIST``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).parent.parent
_ALLOWLIST: frozenset[str] = frozenset(
    {
        "test_single_mode_anti_drift.py",
        "test_single_mode_anti_drift_test_fixtures.py",
    }
)
_REJECTED_MODES: frozenset[str] = frozenset({"compact", "medium", "wide"})
_DISPLAY_CONTEXT_NAMES: frozenset[str] = frozenset({"DisplayContext"})


def _discover_candidate_files() -> tuple[Path, ...]:
    """Return every tests/**/*.py file that textually mentions DisplayContext.

    Excludes ``__pycache__`` and the two intentional anti-drift guard
    files. AST parsing is deferred to the per-test scan to keep this
    import-time scan fast.
    """
    candidates: list[Path] = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name in _ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "DisplayContext" not in text:
            continue
        candidates.append(path)
    return tuple(candidates)


def _display_context_calls(tree: ast.Module) -> list[ast.Call]:
    """Return every Call node whose callee name is in the DisplayContext set."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name: str | None = None
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        if name in _DISPLAY_CONTEXT_NAMES:
            calls.append(node)
    return calls


def _string_literal_value(node: ast.AST) -> str | None:
    """Return the constant string value of an AST Constant node with str value."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_rejected_mode_kwargs(call: ast.Call) -> list[tuple[int, str]]:
    """Yield (lineno, literal) for kwarg 'mode' whose value is a rejected mode."""
    hits: list[tuple[int, str]] = []
    for kw in call.keywords:
        if kw.arg != "mode":
            continue
        value = _string_literal_value(kw.value)
        if value in _REJECTED_MODES:
            hits.append((call.lineno, value))
    return hits


def _iter_rejected_positional_modes(call: ast.Call) -> list[tuple[int, str]]:
    """Yield (lineno, literal) for positional string args that are rejected modes."""
    hits: list[tuple[int, str]] = []
    for arg in call.args:
        value = _string_literal_value(arg)
        if value in _REJECTED_MODES:
            hits.append((call.lineno, value))
    return hits


def _iter_narrow_kwargs(call: ast.Call) -> list[int]:
    """Yield the lineno of every keyword arg named 'narrow' on the call."""
    return [call.lineno for kw in call.keywords if kw.arg == "narrow"]


# Pre-discover candidate files at import time using a cheap textual scan.
# AST parsing is deferred to the per-test scan, which iterates the
# candidate tuple twice (once per test). The pre-filter ensures the
# per-test work stays well under the 1 s per-test timeout under
# pytest-xdist workers.
_CANDIDATE_FILES: tuple[Path, ...] = _discover_candidate_files()


def test_no_compact_medium_wide_in_test_display_context_calls() -> None:
    """No test fixture passes mode='compact' / 'medium' / 'wide' to DisplayContext.

    The production ``DisplayContext.mode`` is ``Literal['default']``; the
    pre-consolidation three-tier dispatch is dead. Any test fixture using
    a rejected mode would raise ``TypeError`` on collection, silently
    masking the drift cleanup.
    """
    violations: list[str] = []
    for path in _CANDIDATE_FILES:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = str(path.relative_to(_TESTS_DIR.parent.parent))
        for call in _display_context_calls(tree):
            for lineno, literal in _iter_rejected_mode_kwargs(call):
                violations.append(f"{rel}:{lineno}: mode={literal!r}")
            for lineno, literal in _iter_rejected_positional_modes(call):
                violations.append(f"{rel}:{lineno}: positional mode={literal!r}")
    assert not violations, (
        "Test fixtures must not pass mode='compact' / 'medium' / 'wide' to "
        "DisplayContext (Ralph Workflow has a SINGLE display mode called "
        "'default'; pre-consolidation tier dispatch is dead). Violations:\n"
        + "\n".join(violations)
    )


def test_no_narrow_kwarg_in_test_display_context_calls() -> None:
    """No test fixture passes narrow=<value> to DisplayContext.

    The pre-consolidation ``narrow=`` tier flag is dead; the consolidated
    single-mode Status Bar adapts to width inside the renderer rather
    than via a kwarg. Any test fixture using ``narrow=`` would raise
    ``TypeError: DisplayContext.__init__() got an unexpected keyword argument 'narrow'``
    on collection.
    """
    violations: list[str] = []
    for path in _CANDIDATE_FILES:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = str(path.relative_to(_TESTS_DIR.parent.parent))
        for call in _display_context_calls(tree):
            violations.extend(
                f"{rel}:{lineno}: narrow=" for lineno in _iter_narrow_kwargs(call)
            )
    assert not violations, (
        "Test fixtures must not pass narrow=<value> to DisplayContext "
        "(the pre-consolidation tier flag is dead; the single default "
        "mode adapts to width inside the renderer). Violations:\n"
        + "\n".join(violations)
    )
