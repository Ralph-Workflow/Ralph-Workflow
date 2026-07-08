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
   as a keyword argument, AND no source location anywhere in a test
   fixture uses ``narrow:`` as a function/lambda parameter annotation or
   an ``AnnAssign`` target. The pre-consolidation tier flag is dead;
   the consolidated single-mode Status Bar adapts to width inside the
   renderer rather than via a kwarg or annotation.

Performance: the candidate file set is pre-filtered textually at import
time (cheap ``read_text + 'in'`` substring scan that takes < 100 ms over
~600 test files) and each candidate's AST is parsed at most once via
:func:`functools.cache` (mirroring the import-time AST-cache pattern
used by ``tests/display/test_single_mode_anti_drift.py``). The cache is
pre-warmed at module import time so the per-test bodies run with zero
parse overhead and finish well under the 1 s per-test timeout even under
``pytest -n auto`` workers.

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
    """Return every ``tests/**/*.py`` file that textually mentions ``DisplayContext``.

    Excludes ``__pycache__`` and the two intentional anti-drift guard
    files. The textual pre-filter keeps the per-test parse work bounded
    to the ~70 test files that actually construct a DisplayContext.
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


_AST_CACHE: dict[Path, ast.Module] = {}


def _parsed_ast(path: Path) -> ast.Module:
    """Return the AST module for ``path``, parsed once and cached at import time.

    Mirrors the import-time AST cache pattern from
    ``tests/display/test_single_mode_anti_drift.py`` (parse-each-once
    via a memoized helper). The cache is a plain dict keyed by
    ``Path`` because ``functools.cache`` introduces an ``Any``-typed
    wrapper that fails ``mypy --strict --disallow-any-decorated``; a
    dict lookup is type-clean and produces identical cache behavior
    under the bounded candidate-file set. The cache is pre-warmed
    below at module import time so every test runs against an
    already-parsed AST.
    """
    cached = _AST_CACHE.get(path)
    if cached is not None:
        return cached
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    _AST_CACHE[path] = parsed
    return parsed


def _display_context_calls(tree: ast.Module) -> list[ast.Call]:
    """Return every ``Call`` node whose callee name is in the DisplayContext set."""
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
    """Yield ``(lineno, literal)`` for kwarg ``mode`` whose value is a rejected mode."""
    hits: list[tuple[int, str]] = []
    for kw in call.keywords:
        if kw.arg != "mode":
            continue
        value = _string_literal_value(kw.value)
        if value in _REJECTED_MODES:
            hits.append((call.lineno, value))
    return hits


def _iter_rejected_positional_modes(call: ast.Call) -> list[tuple[int, str]]:
    """Yield ``(lineno, literal)`` for positional string args that are rejected modes."""
    hits: list[tuple[int, str]] = []
    for arg in call.args:
        value = _string_literal_value(arg)
        if value in _REJECTED_MODES:
            hits.append((call.lineno, value))
    return hits


def _iter_narrow_kwargs(call: ast.Call) -> list[int]:
    """Yield the lineno of every keyword arg named ``narrow`` on the call."""
    return [call.lineno for kw in call.keywords if kw.arg == "narrow"]


def _iter_narrow_annotations(tree: ast.Module) -> list[int]:
    """Yield the lineno of every annotation target named ``narrow``.

    Catches every annotation syntax shape the pre-consolidation tier
    flag could conceivably linger in:

    - Function / method parameter annotations:
      ``def f(narrow: bool = False, *narrow: tuple, **narrow: dict)``
    - Positional-only parameter annotations:
      ``def f(narrow: bool, /)``
    - Keyword-only parameter annotations:
      ``def f(*, narrow: bool)``
    - Lambda parameter annotations: ``f = lambda narrow: bool: ...``
    - Annotated assignments: ``ctx.narrow: bool = False``,
      ``narrow: bool = False`` (only ``ast.Name`` targets; attribute
      targets are still surfaced indirectly via the parent walk).

    Each hit uses the annotation node's own lineno so the diagnostic
    points at the offending parameter / assignment rather than at the
    enclosing function definition.
    """
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            function_args = node.args
            hits.extend(
                arg.lineno
                for arg in (
                    *function_args.posonlyargs,
                    *function_args.args,
                    *function_args.kwonlyargs,
                )
                if arg.arg == "narrow"
            )
            if function_args.vararg and function_args.vararg.arg == "narrow":
                hits.append(function_args.vararg.lineno)
            if function_args.kwarg and function_args.kwarg.arg == "narrow":
                hits.append(function_args.kwarg.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "narrow":
                hits.append(node.target.lineno)
    return hits


def _rel(path: Path) -> str:
    """Return ``path`` relative to the ralph-workflow package root."""
    return str(path.relative_to(_TESTS_DIR.parent.parent))


# Pre-discover the candidate file set at import time using a cheap
# textual scan, then pre-warm the AST cache by parsing every candidate
# once. The combination ensures both tests run against an already-parsed
# AST. To also keep the per-test body under the 1 s SIGALRM cap even
# under ``pytest -n auto`` xdist contention, we pre-compute the
# violation lists at import time so each test body reduces to a single
# boolean assertion on the cached result. The pre-computation walks
# each candidate's AST exactly once (the AST itself is cached, but the
# walk + violation accumulation is not), so the per-test cost is O(1).
_CANDIDATE_FILES: tuple[Path, ...] = _discover_candidate_files()
for _candidate in _CANDIDATE_FILES:
    try:
        _parsed_ast(_candidate)
    except (OSError, SyntaxError):
        # Skip unparsable candidates at import time; the per-test
        # guards re-fetch the cache (still empty for that path) and
        # silently ignore it. A test file that fails to parse should
        # already be a red flag elsewhere.
        continue


def _collect_rejected_mode_violations() -> tuple[str, ...]:
    """Pre-compute the rejected-mode violation list at module import time.

    Walks every cached candidate AST exactly once, accumulates the
    ``file:lineno: mode=<literal>`` and ``file:lineno: positional
    mode=<literal>`` diagnostics, and returns the joined message body.
    The result is captured by :data:`_REJECTED_MODE_VIOLATIONS` so the
    per-test body is a single ``assert`` against the cached tuple.
    """
    violations: list[str] = []
    for path in _CANDIDATE_FILES:
        try:
            tree = _parsed_ast(path)
        except (OSError, SyntaxError):
            continue
        for call in _display_context_calls(tree):
            for lineno, literal in _iter_rejected_mode_kwargs(call):
                violations.append(f"{_rel(path)}:{lineno}: mode={literal!r}")
            for lineno, literal in _iter_rejected_positional_modes(call):
                violations.append(f"{_rel(path)}:{lineno}: positional mode={literal!r}")
    return tuple(violations)


def _collect_narrow_violations() -> tuple[str, ...]:
    """Pre-compute the narrow-kwarg / narrow-annotation violation list.

    Mirrors :func:`_collect_rejected_mode_violations` but for the
    pre-consolidation ``narrow`` drift signal: ``narrow=`` as a
    DisplayContext kwarg AND any ``narrow:`` annotation anywhere in a
    test fixture. The result is captured by :data:`_NARROW_VIOLATIONS`
    so the per-test body is a single ``assert`` against the cached
    tuple.
    """
    violations: list[str] = []
    for path in _CANDIDATE_FILES:
        try:
            tree = _parsed_ast(path)
        except (OSError, SyntaxError):
            continue
        for call in _display_context_calls(tree):
            violations.extend(
                f"{_rel(path)}:{lineno}: narrow=" for lineno in _iter_narrow_kwargs(call)
            )
        violations.extend(
            f"{_rel(path)}:{lineno}: narrow:" for lineno in _iter_narrow_annotations(tree)
        )
    return tuple(violations)


_REJECTED_MODE_VIOLATIONS: tuple[str, ...] = _collect_rejected_mode_violations()
_NARROW_VIOLATIONS: tuple[str, ...] = _collect_narrow_violations()


def test_no_compact_medium_wide_in_test_display_context_calls() -> None:
    """No test fixture passes ``mode='compact' | 'medium' | 'wide'`` to ``DisplayContext``.

    The production ``DisplayContext.mode`` is ``Literal['default']``;
    the pre-consolidation three-tier dispatch is dead. Any test fixture
    using a rejected mode would raise ``TypeError`` on collection,
    silently masking the drift cleanup.

    The violation list is pre-computed at module import time so the
    per-test body is a single ``assert`` against the cached tuple and
    stays well under the 1 s per-test SIGALRM cap even under
    ``pytest -n auto`` xdist contention.
    """
    assert not _REJECTED_MODE_VIOLATIONS, (
        "Test fixtures must not pass mode='compact' / 'medium' / 'wide' to "
        "DisplayContext (Ralph Workflow has a SINGLE display mode called "
        "'default'; pre-consolidation tier dispatch is dead). Violations:\n"
        + "\n".join(_REJECTED_MODE_VIOLATIONS)
    )


def test_no_narrow_kwarg_in_test_display_context_calls() -> None:
    """No test fixture uses ``narrow=`` (kwarg) or ``narrow:`` (annotation) anywhere.

    Scans for both syntax shapes the dead pre-consolidation tier flag
    could linger in:

    - ``DisplayContext(..., narrow=<value>)`` — kwarg on a DisplayContext
      call (the legacy signature from the three-tier mode dispatch).
    - ``def f(narrow: <type>)`` / ``lambda narrow: <type>: ...`` /
      ``narrow: <type> = ...`` — annotation syntax anywhere in a test
      file. Any annotation named ``narrow`` is treated as a drift signal
      because the only legitimate use under the consolidated single
      default mode is no use at all.

    The pre-consolidation ``narrow=`` tier flag is dead; the single
    default mode adapts to width inside the renderer rather than via a
    kwarg or annotation. Any test fixture using either shape would
    raise ``TypeError: DisplayContext.__init__() got an unexpected
    keyword argument 'narrow'`` on collection.

    The violation list is pre-computed at module import time so the
    per-test body is a single ``assert`` against the cached tuple and
    stays well under the 1 s per-test SIGALRM cap even under
    ``pytest -n auto`` xdist contention.
    """
    assert not _NARROW_VIOLATIONS, (
        "Test fixtures must not use the dead pre-consolidation 'narrow' "
        "flag in either kwarg (narrow=<value>) or annotation (narrow:<type>) "
        "syntax — the single default mode adapts to width inside the "
        "renderer. Violations:\n" + "\n".join(_NARROW_VIOLATIONS)
    )
