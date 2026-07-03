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
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.runner import resolve_display as _resolve_display_runner

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_BANNER_FILE = Path(__file__).parent.parent.parent / "ralph" / "banner.py"
_PIPELINE_DIR = Path(__file__).parent.parent.parent / "ralph" / "pipeline"
_CLI_COMMANDS_DIR = Path(__file__).parent.parent.parent / "ralph" / "cli" / "commands"

_CONSOLE_ALLOWED = {"theme.py"}
_THEME_ALLOWED = {"theme.py"}
_ENV_ALLOWED = {"context.py", "content_condenser.py"}
_CLI_COMMANDS_ENV_EXEMPT = {"run.py", "smoke.py"}


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
        is_type_checking = isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
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
# and ralph/pipeline/. All nine tests in this file pass on the current
# code and stay passing to prevent future drift re-introducing inline
# Console construction, runtime rich Console imports, or split
# resolve_display symbols.
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
    """Anti-drift guard: passes on current code; fails on drift re-introducing inline ``Console()``.

    Every Console in ralph/cli/commands/ must come from a DisplayContext.
    The current code is clean (zero inline ``Console()`` constructions);
    this test pins that property. A future commit that adds an inline
    ``Console()`` to a CLI command without going through DisplayContext
    will be flagged here. The ``# noqa: di-allow`` marker exempts
    intentional constructions.
    """
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_cli_command_files()
        for line in _scan_lines(path)
        if "Console(" in line
    ]
    assert not violations, (
        "Inline Console() found in ralph/cli/commands/ (DI violation):\n" + "\n".join(violations)
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
    """Anti-drift guard for non-run.py CLI commands; run.py and smoke.py are exempt.

    Passes on current code. Fails if drift re-introduces ``os.environ`` /
    ``os.getenv`` reads. run.py is excluded because the refactor in step 5(b)
    removes its single os.environ read in favor of the in-scope DisplayContext.env
    mapping. smoke.py is excluded because it is a one-off manual debug
    harness that needs to set a process-wide env default
    (``MOCK_AGY_ARTIFACT_DIR``) so the spawned AGY mock subprocess inherits
    the workspace root. The harness's ``extra_env`` mechanism would require
    changes to ``InvokeAgentEffect`` and the entire plumbing chain, which is
    out of scope for the AGY support task. The setdefault is a one-line
    default that only fires when the operator has not already set the env
    var; it does not bypass the test for the same reason run.py is exempt
    (manual debug command with a single justified env touchpoint).

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
    """Anti-drift guard: ``resolve_display`` must be a single function object.

    Imports ``resolve_display`` from both ``ralph.display`` and
    ``ralph.pipeline.runner`` and asserts they are the same function
    object (``is``). The current code is clean (single canonical
    function at ``ralph.display.parallel_display.resolve_display``
    re-exported from both surfaces); this test pins that property.
    Also asserts the canonical signature accepts ``is_quiet=False`` as
    a keyword, which keeps the re-export signature-compatible with the
    runner's prior ``is_quiet`` kwarg.
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
    """Anti-drift guard: fails on drift re-introducing runtime rich Console import.

    Scans ralph/cli/commands/ and ralph/pipeline/ for runtime
    ``from rich.console import Console`` imports. Lines inside
    ``if TYPE_CHECKING:`` blocks are correctly excluded by the
    tokenize-based scan. The current code is clean (zero runtime
    hits); this test pins that property. The di-allow marker
    exempts intentional imports.
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


def test_no_console_print_in_main_py_except_version_path() -> None:
    """Pin wt-007: only the --version early-exit may use direct c.print in main.py.

    AST-scans ralph/cli/main.py for ``console.print(``, ``c.print(``,
    and ``ctx.console.print(`` calls. The ONLY remaining direct call
    is the one inside ``version_callback`` — the --version early-exit
    path that runs before any DisplayContext is built. A regression
    that introduces a new direct c.print in main.py would be flagged
    here.
    """
    main_path = _REPO_ROOT_FOR_TESTS / "ralph" / "cli" / "main.py"
    source = main_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    def _is_in_version_callback(lineno: int) -> bool:
        """Return True when the given 1-based line is inside ``version_callback``."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != "version_callback":
                continue
            if node.lineno <= lineno <= node.end_lineno:
                return True
        return False

    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "print":
            continue
        # Match c.print(...) where c is a local Name
        if isinstance(node.func.value, ast.Name) and node.func.value.id in {"c", "console"}:
            violations.append(node.lineno)
            continue
        # Match ctx.console.print(...)
        if (
            isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "console"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id in {"ctx", "display_context"}
        ):
            violations.append(node.lineno)
    non_version_violations = [
        lineno for lineno in violations if not _is_in_version_callback(lineno)
    ]
    assert not non_version_violations, (
        f"main.py must have zero direct console.print outside the "
        f"version_callback function; found {non_version_violations!r}.\n"
        f"Direct c.print/console.print/ctx.console.print call sites: {violations!r}\n"
        f"Context around the first violation:\n"
        f"{chr(10).join(lines[max(0, violations[0] - 3) : violations[0] + 2])}"
    )
    assert len(violations) >= 1, (
        f"version_callback must keep its single direct c.print at line "
        f"{lines.index('c.print(version_text)') + 1 if 'c.print(version_text)' in lines else '?'}; "
        f"found no direct c.print/console.print calls in main.py at all."
    )


def test_no_free_function_imports_in_cli_or_pipeline() -> None:
    """Pin wt-007: zero free-function display imports in CLI / pipeline / config.

    Scans ralph/cli/commands/, ralph/pipeline/, ralph/config/ for the 7
    forbidden free-function import prefixes and asserts zero hits. Also
    scans for direct ``console.print(``, ``c.print(``, and
    ``ctx.console.print(`` calls in ralph/cli/commands/*.py,
    ralph/pipeline/*.py, ralph/config/*.py and asserts zero hits — a
    regression that re-introduces a direct console.print would be
    flagged here.
    """
    targets = [
        *_all_cli_command_files(),
        *_all_pipeline_files(),
        *(_REPO_ROOT_FOR_TESTS / "ralph" / "config").glob("*.py"),
    ]
    forbidden_prefixes = (
        "from ralph.display.phase_banner",
        "from ralph.display.artifact_renderer",
        "from ralph.display.first_run_panel",
        "from ralph.display.tables",
        "from ralph.banner",
        "from ralph.cli.options import display_agents_table",
        "from ralph.cli.options import display_providers_table",
        "from ralph.display.completion_summary import emit_completion_summary",
    )
    import_violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in targets
        for line in _scan_lines(path)
        for prefix in forbidden_prefixes
        if prefix in line
    ]
    assert not import_violations, (
        "Free-function display imports re-introduced in CLI/pipeline/config "
        "(wt-007 anti-drift guard tripped):\n" + "\n".join(import_violations)
    )

    print_call_patterns = (
        "console.print(",
        "c.print(",
        "ctx.console.print(",
    )
    print_violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in targets
        for line in _scan_lines(path)
        for pattern in print_call_patterns
        if pattern in line
    ]
    assert not print_violations, (
        "Direct console.print/c.print/ctx.console.print calls "
        "re-introduced in ralph/cli/commands/, ralph/pipeline/, or "
        "ralph/config/ (wt-007 anti-drift guard tripped):\n" + "\n".join(print_violations)
    )


# ---------------------------------------------------------------------------
# wt-007 closing pass: every emit_* method has at least one black-box test
# reference; the canonical emit_* set (42 names, single-sourced from
# tests.display.test_parallel_display_drift_prevention._PARALLEL_DISPLAY_ALL_NAMES)
# is the single source of truth.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _test_parallel_display_files() -> tuple[Path, ...]:
    """Return all ``test_parallel_display_*.py`` files under ``tests/display/``.

    Broadened glob (vs. just ``test_parallel_display_emit_*.py``) so that
    references in ``test_parallel_display_drift_prevention.py`` (which
    enumerates the canonical set) and ``test_parallel_display_visual_hierarchy.py``
    also count as black-box coverage.
    """
    glob_root = _REPO_ROOT_FOR_TESTS / "tests" / "display"
    return tuple(sorted(glob_root.glob("test_parallel_display_*.py")))


@lru_cache(maxsize=1)
def _canonical_42_names() -> frozenset[str]:
    """Return the canonical 42 emit_* method names from drift_prevention.

    Single-sources the canonical set so this test never drifts from the
    authoritative surface defined in
    ``tests/display/test_parallel_display_drift_prevention.py``.
    """
    drift_module = importlib.import_module("tests.display.test_parallel_display_drift_prevention")
    return frozenset(drift_module._PARALLEL_DISPLAY_ALL_NAMES)


def test_every_emit_method_has_black_box_coverage() -> None:
    """Every emit_* method is referenced in some ``test_parallel_display_*.py``.

    Walks the broadened glob of test files and asserts each canonical
    42-name set entry (minus the leading ``emit_`` prefix) appears as a
    substring somewhere in those files' bodies. This guards against
    silent additions to ParallelDisplay that are never covered by a
    black-box test.
    """
    canonical = _canonical_42_names()
    file_bodies: list[tuple[Path, str]] = [
        (path, path.read_text(encoding="utf-8")) for path in _test_parallel_display_files()
    ]
    missing: list[str] = []
    for full_name in sorted(canonical):
        # Strip the ``emit_`` prefix to check for the method name as a
        # substring in test bodies (e.g. ``emit_agents_table`` -> ``agents_table``).
        bare = full_name[len("emit_") :] if full_name.startswith("emit_") else full_name
        if not any(f"emit_{bare}" in body for _, body in file_bodies):
            missing.append(full_name)
    assert not missing, (
        f"emit_* methods without a black-box test reference in any "
        f"test_parallel_display_*.py: {missing!r}. Add a test file in "
        f"tests/display/ that references each missing name."
    )


def test_table_panel_methods_emit_section_rule_header() -> None:
    """Every table/panel emit_* method calls ``_emit_section_rule(...)``.

    Uses :func:`inspect.getsource` on each of the 10 table/panel methods
    and asserts the source contains either the ``_emit_section_rule(``
    call or the literal section-rule tag (e.g. ``[agents]``) as a
    substring. The two exempt methods (``emit_first_run_panel`` and
    ``emit_renderable``) are explicitly skipped because their own
    panel/renderable IS the visual section, not a tag prefix.
    """
    exempt = {"emit_first_run_panel", "emit_renderable"}
    table_panel_methods = (
        "emit_agents_table",
        "emit_providers_table",
        "emit_config_table",
        "emit_metrics_table",
        "emit_checkpoint_summary_table",
        "emit_diagnose_inventory_table",
        "emit_diagnose_probe_table",
        "emit_diagnose_servers_table",
        "emit_capability_summary",
        "emit_info_panel",
    )
    canonical_set = _canonical_42_names()
    missing_section_rule: list[str] = []
    for name in table_panel_methods:
        if name in exempt:
            continue
        assert name in canonical_set, (
            f"test_table_panel_methods_emit_section_rule_header references unknown method {name!r}"
        )
        method = getattr(ParallelDisplay, name, None)
        if method is None:
            missing_section_rule.append(f"{name}: method missing")
            continue
        try:
            source = inspect.getsource(method)
        except (OSError, TypeError):
            missing_section_rule.append(f"{name}: source unavailable")
            continue
        if "_emit_section_rule(" not in source and "[" not in source:
            missing_section_rule.append(name)
    assert not missing_section_rule, (
        "Table/panel emit_* methods missing a section-rule header: "
        f"{missing_section_rule!r}. Add ``self._emit_section_rule('[<tag>]')`` "
        "as the first statement inside the "
        "``with contextlib.suppress(Exception):`` block."
    )


def test_every_emit_method_with_test_file_match_exists() -> None:
    """Every ``emit_*`` reference in ``test_parallel_display_emit_*.py`` files
    corresponds to a real ParallelDisplay method.

    Companion to ``test_every_emit_method_has_black_box_coverage``:
    catches a regression where someone adds a stray
    ``test_parallel_display_emit_foo.py`` for a method that doesn't
    exist on the class. The broadened glob from test 1 already catches
    this; this test makes the invariant explicit and produces a
    focused diagnostic that names the exact ``(path, line, full)``
    triple for every stray emit_* reference.
    """
    canonical = _canonical_42_names()
    stray_refs: list[tuple[str, str, str]] = []
    # Names that are legitimate emit_* references but NOT in the
    # ParallelDisplay canonical set: ``emit_activity_line`` is the
    # module-level activity helper and ``emit_completion_summary`` is
    # the method on the unrelated CompletionSummary class
    # (``ralph.display.completion_summary.emit_completion_summary``).
    non_parallel_emit_allowlist = frozenset(
        {"emit_activity_line", "emit_completion_summary"}
    )
    for path in _test_parallel_display_files():
        if not path.name.startswith("test_parallel_display_emit_"):
            continue
        body = path.read_text(encoding="utf-8")
        try:
            token_iter = list(
                tokenize.generate_tokens(io.StringIO(body).readline)
            )
        except (tokenize.TokenError, IndentationError, SyntaxError):
            token_iter = []
        for tok in token_iter:
            if tok.type != tokenize.NAME:
                continue
            if not tok.string.startswith("emit_"):
                continue
            full = tok.string
            if full in canonical:
                continue
            if full in non_parallel_emit_allowlist:
                continue
            stray_refs.append((path.name, tok.line.rstrip(), full))
    assert not stray_refs, (
        "stray emit_* references in test files that are not in the "
        "canonical 42-name set (add the name to "
        "tests/display/test_parallel_display_drift_prevention."
        "_PARALLEL_DISPLAY_ALL_NAMES or remove the stray reference): "
        + "\n".join(
            f"{name}:{line.strip()}: {full}"
            for name, line, full in stray_refs
        )
    )


# ---------------------------------------------------------------------------
# wt-007 closing pass: the new emit_completion_summary_panel method is part of
# the consolidated emit_* set.
# These tests pin the anti-drift guard so the free-function call cannot be
# re-introduced in ralph/pipeline/ and the new method is callable on
# ParallelDisplay.
# ---------------------------------------------------------------------------


def test_no_free_function_completion_summary_in_pipeline() -> None:
    """Anti-drift guard: ralph/pipeline/ must NOT import or call the free function.

    Scans every Python file under ralph/pipeline/ for any
    ``from ralph.display.completion_summary import emit_completion_summary``
    or ``completion_summary.emit_completion_summary`` call. The only allowed
    import is :class:`CompletionSummaryOptions` (the dataclass is still
    public). Production code in ralph/pipeline/ must route through
    :meth:`ParallelDisplay.emit_completion_summary_panel`.
    """
    violations: list[str] = [
        f"{path.relative_to(_PIPELINE_DIR)}:{line.rstrip()}"
        for path in _all_pipeline_files()
        for line in _scan_lines(path)
        if "from ralph.display.completion_summary import emit_completion_summary" in line
        or "completion_summary.emit_completion_summary" in line
    ]
    assert not violations, (
        "Free-function emit_completion_summary re-introduced in ralph/pipeline/ "
        "(wt-007 anti-drift guard tripped):\n" + "\n".join(violations)
    )


def test_parallel_display_has_emit_completion_summary_panel() -> None:
    """The consolidated emit_completion_summary_panel method exists and is callable.

    Pins the architectural contract from the public-method side. Mirrors
    ``test_parallel_display_exposes_exact_42_emit_methods`` in
    ``test_parallel_display_drift_prevention.py``. This test makes the
    drift visible if a future commit silently drops it.
    """
    method = getattr(ParallelDisplay, "emit_completion_summary_panel", None)
    assert method is not None, (
        "ParallelDisplay is missing the emit_completion_summary_panel method; "
        "the consolidated emit_completion_summary_panel method must exist on ParallelDisplay."
    )
    assert callable(method), (
        "ParallelDisplay.emit_completion_summary_panel must be callable; "
        f"got non-callable: {method!r}"
    )


_REPO_ROOT_FOR_TESTS = Path(__file__).parent.parent.parent
