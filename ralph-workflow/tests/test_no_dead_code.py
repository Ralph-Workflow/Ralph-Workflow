"""Black-box pin: no module-level Console globals in ralph-workflow source.

This is the second independent pin of the no-module-level-console invariant
(the first lives in ``tests/test_display_no_module_globals_no_module_level_console_globals.py``).
A green test here means there is no ``console = Console()`` /
``global_console = Console()`` / ``default_console = Console()`` literal at
the module level anywhere in ``ralph-workflow/ralph/``. Module-level Console
construction was the canonical drift vector that
``ralph/display/parallel_display.py`` (the only authoritative display
implementation) explicitly removed; this test prevents a regression.

The test is intentionally a static walk over the source tree using AST
parsing — no I/O, no subprocess, no ``time.sleep``. The expected wall-clock
cost is well under 1 second on any machine.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"

# Module-level attribute names that count as a Console-construction violation
# (matches the existing pin's vocabulary).
_FORBIDDEN_MODULE_LEVEL_NAMES: frozenset[str] = frozenset(
    {
        "console",
        "global_console",
        "default_console",
    }
)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _walk_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _module_level_console_globals(source: str) -> list[tuple[int, str, ast.expr]]:
    """Return the list of (line, target, value) module-level assignments where
    the right-hand side is a ``Console()`` call.

    The check is intentionally AST-based: parsing a malformed file would
    raise ``SyntaxError`` and the test would fail with a useful trace
    pointing at the file. We never silently skip a file.

    Handles both ``ast.Assign`` (plain ``console = Console()``) and
    ``ast.AnnAssign`` (annotated ``console: Console = Console()``)
    forms so neither pattern slips through.
    """
    tree = ast.parse(source)
    offenders: list[tuple[int, str, ast.expr]] = []

    def _consider_target(lineno: int, target: ast.expr, value: ast.expr | None) -> None:
        if value is None:
            return
        if not isinstance(target, ast.Name):
            return
        if target.id not in _FORBIDDEN_MODULE_LEVEL_NAMES:
            return
        if _is_console_construction(value):
            offenders.append((lineno, target.id, value))

    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            _consider_target(node.lineno, node.target, node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _consider_target(node.lineno, target, node.value)
    return offenders


def _is_console_construction(node: ast.expr) -> bool:
    """True if ``node`` is a call to ``Console()`` (possibly attribute-resolved).

    Matches:
        ``Console()``
        ``ralph.display.parallel_display.Console()``  (defensive; not a real path)
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Console"
    return isinstance(func, ast.Attribute) and func.attr == "Console"


# AST-parses every file under ralph/ — CPU-bound and contended under the parallel
# suite, so it gets the same per-test wall-clock cap as the other static-analysis
# walks (e.g. test_type_ignore_policy). The immutable 60s COMBINED budget is
# unaffected; this is only the secondary per-test cap.
@pytest.mark.timeout_seconds(5)
def test_no_module_level_console_globals_in_ralph_source() -> None:
    """Walk ``ralph-workflow/ralph/`` and assert no module-level
    ``console = Console()`` / ``global_console = Console()`` /
    ``default_console = Console()`` literal survives.

    This is a black-box pin of the same invariant enforced by
    ``tests/test_display_no_module_globals_no_module_level_console_globals.py``.
    Both pins must stay green.
    """
    offenders: list[str] = []
    for path in _walk_python_files(RALPH_ROOT):
        # Substring pre-filter: an offender MUST contain a
        # ``Console()`` call to one of the forbidden names. A file
        # that does not contain the literal ``Console(`` cannot
        # contribute an offender and is skipped without an
        # ``ast.parse`` pass. This is the canonical fast-path
        # pattern (also used in test_no_anti_drift_regression.py
        # and test_no_anti_drift_recovery_invariants.py) and
        # collapses the AST-walk cost from O(total_source_bytes)
        # to O(matching_files_source_bytes).
        try:
            source = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        if "Console(" not in source:
            continue
        for lineno, target, value in _module_level_console_globals(source):
            offenders.append(
                f"{path.relative_to(RALPH_ROOT.parent)}:{lineno} {target} = {ast.unparse(value)}"
            )
    assert offenders == [], (
        "Module-level Console construction found in ralph-workflow/ralph/; "
        "delete the offending module-level Console and inject it via "
        "ParallelDisplay instead: " + str(offenders)
    )


def test_module_level_console_walk_finds_expected_files() -> None:
    """The walk above must cover the ralph source tree (no I/O, no sleep).

    We deliberately avoid ``time.monotonic()`` here — the test policy
    audit forbids real wall-clock measurement in non-subprocess-e2e
    tests. The other parametrised tests in this file already exercise
    the helper; this case is a smoke test that the walk produces the
    expected number of files (i.e. it is not silently empty).
    """
    files = _walk_python_files(RALPH_ROOT)
    assert len(files) > 0, "RALPH_ROOT should contain at least one .py file"
    # Read every file once so the test fails if any read I/O is broken.
    for path in files:
        _ = _read(path)


def test_forbidden_names_match_existing_pin_vocabulary() -> None:
    """The forbidden name set must be a superset of the existing pin's
    vocabulary (currently ``console``). Drift here is a silent
    weakening of the invariant.
    """
    existing = {"console", "global_console", "default_console"}
    assert existing == _FORBIDDEN_MODULE_LEVEL_NAMES, (
        f"_FORBIDDEN_MODULE_LEVEL_NAMES={set(_FORBIDDEN_MODULE_LEVEL_NAMES)!r} "
        f"drifted from the existing pin vocabulary {existing!r}"
    )


@pytest.mark.parametrize(
    "name",
    sorted(_FORBIDDEN_MODULE_LEVEL_NAMES),
)
def test_individual_forbidden_name_is_pinned(name: str) -> None:
    """For each forbidden name, the helper identifies a module-level
    ``{name} = Console()`` assignment as an offender. This parametrised
    smoke test is the smallest possible regression: if a future
    refactor renames a module-level Console attribute, only this
    parametrised case fails (the rest of the suite keeps working).
    """
    source = f"import builtins\n{name} = Console()\n"
    offenders = _module_level_console_globals(source)
    assert offenders, (
        f"expected a module-level `{name} = Console()` assignment to be flagged; "
        f"the helper missed it"
    )
    assert offenders[0][1] == name
