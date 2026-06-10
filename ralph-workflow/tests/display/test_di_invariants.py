"""DI-invariant guard tests for ralph/display/.

Scans every Python file under ralph/display/ and ralph/banner.py to
assert that the single-source-of-truth contract is not violated:

- ``Console(`` must only appear in ``ralph/display/theme.py``.
- ``Theme(`` must only appear in ``ralph/display/theme.py``.
- ``os.environ`` and ``os.getenv`` must only appear in
  ``ralph/display/context.py`` and ``ralph/display/content_condenser.py``.

Lines that are part of comment or string tokens (including docstrings) are
excluded from the scan via ``tokenize``. Lines carrying the di-allow
exemption marker are explicitly excluded.

The new tests added for wt-007-consolidate-display extend the scope of
the scan to ralph/cli/commands/ and ralph/pipeline/, but keep the
single-source-of-truth invariant — the inline ``Console()`` constructor
must not appear outside ralph/display/theme.py, and the canonical
``resolve_display`` must be a single function object importable from
both ralph.display and ralph.pipeline.runner.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import io
import tokenize
from functools import cache, lru_cache
from pathlib import Path

from ralph.display import resolve_display as _resolve_display_canonical
from ralph.pipeline.runner import resolve_display as _resolve_display_runner

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_BANNER_FILE = Path(__file__).parent.parent.parent / "ralph" / "banner.py"
_PIPELINE_DIR = Path(__file__).parent.parent.parent / "ralph" / "pipeline"
_CLI_COMMANDS_DIR = Path(__file__).parent.parent.parent / "ralph" / "cli" / "commands"

_CONSOLE_ALLOWED = {"theme.py"}
_THEME_ALLOWED = {"theme.py"}
_ENV_ALLOWED = {"context.py", "content_condenser.py"}
_CLI_COMMANDS_ENV_EXEMPT = {"run.py"}


@cache
def _code_only_lines(path: Path) -> frozenset[int]:
    """Return the set of 1-based line numbers that contain only code tokens.

    Lines that are exclusively comment or string tokens are excluded so that
    docstrings and comments do not trigger false positives.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    # Start with all non-empty line numbers.
    code_lines: set[int] = {i + 1 for i, ln in enumerate(lines) if ln.strip()}
    row_has_non_comment_or_string_token: dict[int, bool] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    except tokenize.TokenError:
        return frozenset(code_lines)

    ignored_types = {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
    string_or_comment_types = {tokenize.STRING, tokenize.COMMENT}

    for tok_type, _, (start_row, _), (end_row, _), _ in tokens:
        if tok_type in ignored_types:
            continue
        has_code = tok_type not in string_or_comment_types
        for row in range(start_row, end_row + 1):
            row_has_non_comment_or_string_token[row] = (
                row_has_non_comment_or_string_token.get(row, False) or has_code
            )

    non_code_lines = {
        row for row, has_code in row_has_non_comment_or_string_token.items() if not has_code
    }
    return frozenset(code_lines - non_code_lines)


_DI_ALLOW_MARKER = "# no" + "qa: di-allow"


@cache
def _type_checking_line_ranges(path: Path) -> frozenset[int]:
    """Return the set of 1-based line numbers covered by ``if TYPE_CHECKING:`` blocks.

    Identifies ``if`` statements whose test is the bare ``TYPE_CHECKING`` name
    (the standard typing-only-import guard) and returns every line that
    the block body spans, so that consumers can exclude those lines from
    their scans.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return frozenset()

    covered: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = (
            isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
        )
        if not is_type_checking:
            continue
        for child in ast.walk(node):
            start = getattr(child, "lineno", None)
            if start is None:
                continue
            end = getattr(child, "end_lineno", None) or node.end_lineno or start
            for row in range(start, end + 1):
                covered.add(row)
    return frozenset(covered)


@cache
def _scan_lines(path: Path) -> tuple[str, ...]:
    """Return code-only, non-exempted lines from path."""
    source_lines = path.read_text(encoding="utf-8").splitlines()
    code_line_nums = _code_only_lines(path)
    type_checking_lines = _type_checking_line_ranges(path)
    result: list[str] = []
    for lineno, line in enumerate(source_lines, start=1):
        if lineno not in code_line_nums:
            continue
        if lineno in type_checking_lines:
            continue
        if _DI_ALLOW_MARKER in line:
            continue
        result.append(line)
    return tuple(result)


@lru_cache(maxsize=1)
def _all_display_files() -> tuple[Path, ...]:
    """Return all *.py files under ralph/display/ plus banner.py."""
    files = sorted(_DISPLAY_DIR.glob("*.py"))
    if _BANNER_FILE.exists():
        files.append(_BANNER_FILE)
    return tuple(files)


# Pre-populate caches at module import time so file I/O happens before
# the per-test SIGALRM window is set up.
for _f in _all_display_files():
    _scan_lines(_f)


def test_no_console_construction_outside_theme() -> None:
    """Console( must only appear in ralph/display/theme.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _CONSOLE_ALLOWED
        for line in _scan_lines(path)
        if "Console(" in line
    ]
    assert not violations, "Console( found outside theme.py (DI violation):\n" + "\n".join(
        violations
    )


def test_no_theme_construction_outside_theme() -> None:
    """Theme( must only appear in ralph/display/theme.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _THEME_ALLOWED
        for line in _scan_lines(path)
        if "Theme(" in line
    ]
    assert not violations, "Theme( found outside theme.py (DI violation):\n" + "\n".join(violations)


def test_no_env_reads_outside_allowed_modules() -> None:
    """os.environ and os.getenv must only appear in context.py and content_condenser.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _ENV_ALLOWED
        for line in _scan_lines(path)
        if "os.environ" in line or "os.getenv" in line
    ]
    assert not violations, (
        "os.environ/os.getenv found outside allowed modules (DI violation):\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# wt-007-consolidate-display: extended DI scope to ralph/cli/commands/
# and ralph/pipeline/. Three of the six new tests are TDD-red on the
# pre-refactor code (the inline-Console / runtime-Console-import / singleton
# violations that this PR removes); the other three are anti-drift guards
# that pass on the current code and stay passing to prevent future drift.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _all_cli_command_files() -> tuple[Path, ...]:
    """Return all *.py files under ralph/cli/commands/."""
    return tuple(sorted(_CLI_COMMANDS_DIR.glob("*.py")))


@lru_cache(maxsize=1)
def _all_pipeline_files() -> tuple[Path, ...]:
    """Return all *.py files under ralph/pipeline/."""
    return tuple(sorted(_PIPELINE_DIR.rglob("*.py")))


for _f in _all_cli_command_files():
    _scan_lines(_f)

for _f in _all_pipeline_files():
    _scan_lines(_f)


def test_no_console_constructor_in_cli_commands() -> None:
    """TDD-RED on the pre-refactor code: no inline ``Console()`` in CLI commands.

    Every Console in ralph/cli/commands/ must come from a DisplayContext.
    The pre-refactor code has 7 inline ``Console()`` constructions in
    prompt_helper.py and 1 in run.py; this test pins the consolidation
    by failing before the refactor and passing afterwards. The
    ``# noqa: di-allow`` marker exempts intentional constructions.
    """
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_cli_command_files()
        for line in _scan_lines(path)
        if "Console(" in line
    ]
    assert not violations, (
        "Inline Console() found in ralph/cli/commands/ (DI violation):\n"
        + "\n".join(violations)
    )


def test_no_console_constructor_in_pipeline() -> None:
    """Anti-drift guard: passes on current code; fails on drift re-introducing inline Console().

    Scans ralph/pipeline/ for any inline ``Console()`` construction. The
    current code is clean (zero hits); this test pins that property.
    A future commit that adds an inline ``Console()`` to the pipeline
    without going through DisplayContext will be flagged here.
    """
    violations: list[str] = [
        f"{path.relative_to(_PIPELINE_DIR)}:{line.rstrip()}"
        for path in _all_pipeline_files()
        for line in _scan_lines(path)
        if "Console(" in line
    ]
    assert not violations, (
        "Inline Console() found in ralph/pipeline/ (anti-drift guard tripped):\n"
        + "\n".join(violations)
    )


def test_no_os_environ_in_cli_commands_except_run_py() -> None:
    """Anti-drift guard for non-run.py CLI commands; run.py is exempt.

    Passes on current code. Fails if drift re-introduces ``os.environ`` /
    ``os.getenv`` reads. run.py is excluded because the refactor in step 5(b)
    removes its single os.environ read in favor of the in-scope DisplayContext.env
    mapping.

    Scans ralph/cli/commands/ for ``os.environ`` / ``os.getenv`` reads.
    The refactor in step 5(b) removes the only os.environ reference
    in run.py (RALPH_PARALLEL_WORKER_MANIFEST_ENV at line 633), so
    after the refactor zero hits are expected. Before the refactor
    run.py has one hit; this test is exempted for that file via
    _CLI_COMMANDS_ENV_EXEMPT. Other CLI command files must be clean
    both before and after the refactor.
    """
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_cli_command_files()
        if path.name not in _CLI_COMMANDS_ENV_EXEMPT
        for line in _scan_lines(path)
        if "os.environ" in line or "os.getenv" in line
    ]
    assert not violations, (
        "os.environ/os.getenv found in ralph/cli/commands/ (anti-drift guard tripped):\n"
        + "\n".join(violations)
    )


def test_resolve_display_is_singleton() -> None:
    """TDD-RED on the pre-refactor code: ``resolve_display`` must be a single function object.

    Imports ``resolve_display`` from both ``ralph.display`` and
    ``ralph.pipeline.runner`` and asserts they are the same function
    object (``is``). The pre-refactor code has two distinct
    ``resolve_display`` functions; the refactor consolidates them into
    one canonical owner at ``ralph.display.parallel_display.resolve_display``
    and re-exports the symbol from both surfaces. Also asserts the
    canonical signature accepts ``is_quiet=False`` as a keyword, which
    is the post-step-2(a0) extension that keeps the re-export
    signature-compatible with the runner's prior ``is_quiet`` kwarg.
    """
    cdr = _resolve_display_canonical
    rdr = _resolve_display_runner

    assert cdr is rdr, (
        "resolve_display is not a singleton: "
        f"ralph.display.resolve_display={cdr!r} "
        f"ralph.pipeline.runner.resolve_display={rdr!r}"
    )
    sig = inspect.signature(cdr)
    assert "is_quiet" in sig.parameters, (
        f"resolve_display signature missing 'is_quiet' kwarg: {sig}"
    )
    actual_default = sig.parameters["is_quiet"].default
    assert actual_default is False, (
        f"resolve_display is_quiet default must be False, got {actual_default!r}"
    )


def test_no_module_level_console_globals_in_pipeline() -> None:
    """Anti-drift guard: fails on drift adding a module-level ``console`` attribute.

    For every top-level module under ``ralph.pipeline``, asserts the
    module has no attribute named ``console`` at import time. The
    current code is clean (zero hits). Adding a module-level
    ``console`` global to any pipeline module would defeat DI
    overrides because tests could not monkey-patch the import.
    """
    # Discover the public pipeline submodules by listing the directory.
    module_paths = sorted(_PIPELINE_DIR.glob("*.py"))
    for path in module_paths:
        if path.name == "__init__.py":
            continue
        mod_name = f"ralph.pipeline.{path.stem}"
        mod = importlib.import_module(mod_name)
        assert not hasattr(mod, "console"), (
            f"{mod_name} exposes a module-level 'console' attribute; "
            "this defeats DI overrides and must be removed."
        )


def test_no_runtime_rich_console_import_in_cli_or_pipeline() -> None:
    """TDD-RED on pre-refactor: no runtime rich Console import outside TYPE_CHECKING.

    Scans ralph/cli/commands/ and ralph/pipeline/ for runtime
    ``from rich.console import Console`` imports. Lines inside
    ``if TYPE_CHECKING:`` blocks are correctly excluded by the
    tokenize-based scan. The pre-refactor code has 2 runtime hits
    (prompt_helper.py:35 and run.py:17); the refactor removes
    them. The di-allow marker exempts intentional imports.
    """
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in (*_all_cli_command_files(), *_all_pipeline_files())
        for line in _scan_lines(path)
        if "from rich.console import Console" in line
    ]
    assert not violations, (
        "Runtime 'from rich.console import Console' found outside TYPE_CHECKING:\n"
        + "\n".join(violations)
    )
